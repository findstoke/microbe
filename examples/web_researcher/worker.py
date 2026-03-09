"""
Web Researcher — Example Microbe Worker

A simple worker that processes research workflows.
Run with: arq worker.WorkerSettings
"""

import os
from pathlib import Path

import yaml
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from microbe import Orchestrator, Workflow
from microbe.models import Task, Step


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/researcher",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def process_step(ctx, step_id: str, task_id: str, **kwargs):
    """Process a single step from the shared queue."""
    async with async_session() as db:
        orchestrator = Orchestrator(task_id=task_id, db=db)
        result = await orchestrator.execute_step(step_id)

        if not result.error:
            stmt = select(Task).where(Task.id == task_id)
            task_result = await db.execute(stmt)
            task = task_result.scalar_one_or_none()

            if task:
                workflow_path = (
                    Path("workflows") / f"{task.workflow_id}.yaml"
                )
                if workflow_path.exists():
                    workflow = Workflow.from_yaml(str(workflow_path))
                    ready = await orchestrator.advance(workflow)

                    pool = ctx.get("redis") or ctx.get("pool")
                    for step in ready:
                        await pool.enqueue_job(
                            "process_step",
                            step_id=step.id,
                            task_id=task_id,
                        )

        return result.data if not result.error else {"error": result.error}


async def start_workflow(ctx, workflow_id: str, trigger_data: dict, **kwargs):
    """Start a new research workflow."""
    async with async_session() as db:
        task = Task(workflow_id=workflow_id, trigger_data=trigger_data)
        db.add(task)
        await db.commit()

        workflow = Workflow.from_yaml(f"workflows/{workflow_id}.yaml")
        orchestrator = Orchestrator(task_id=task.id, db=db)
        await orchestrator.initialize_steps(workflow, trigger_data)

        root_steps = await orchestrator._get_steps(status="pending")
        pool = ctx.get("redis") or ctx.get("pool")
        for step in root_steps:
            await pool.enqueue_job(
                "process_step",
                step_id=step.id,
                task_id=task.id,
            )

        print(
            f"🦠 Started workflow '{workflow_id}' "
            f"(task={task.id}, steps={len(root_steps)})"
        )
        return {"task_id": task.id}


async def startup(ctx):
    print("🦠 Web Researcher worker starting...")
    agents_dir = Path("agents")
    if agents_dir.exists():
        agents = list(agents_dir.glob("*.yaml"))
        print(f"   Found {len(agents)} agent(s)")


async def shutdown(ctx):
    print("🦠 Worker shutting down...")
    await engine.dispose()


class WorkerSettings:
    queue_name = "microbe"
    functions = [process_step, start_workflow]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    max_jobs = 10
    job_timeout = 600
