"""
Microbe Runner — Single-process embedded execution engine.

Runs the orchestrator and all agent workers in a single asyncio event loop
with an in-memory queue and SQLite. No external services needed.

Usage:
    runner = EmbeddedRunner()
    await runner.run(workflow="research", trigger={"query": "quantum computing"})
"""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import yaml

from microbe.agent import Agent, UniversalAgent
from microbe.db import create_engine, create_session_factory, init_db
from microbe.models import Step, Task
from microbe.orchestrator import Orchestrator
from microbe.queue import InMemoryQueue
from microbe.workflow import Workflow


class EmbeddedRunner:
    """
    All-in-one runner for local development.

    Discovers agents and workflows from the project directory,
    sets up SQLite + in-memory queue, and runs everything in one process.
    """

    def __init__(
        self,
        *,
        database_url: Optional[str] = None,
        agents_dir: str = "agents",
        workflows_dir: str = "workflows",
        agent_registry: Optional[Dict[str, Type[Agent]]] = None,
    ):
        self.agents_dir = Path(agents_dir)
        self.workflows_dir = Path(workflows_dir)
        self.agent_registry = agent_registry or {}
        self.database_url = database_url
        self.queue = InMemoryQueue()
        self._shutdown = False
        self._agent_configs: Dict[str, dict] = {}
        self._workflows: Dict[str, Workflow] = {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_agents(self) -> Dict[str, dict]:
        """Load agent configs from agents/ directory."""
        agents = {}
        if self.agents_dir.exists():
            for f in sorted(self.agents_dir.glob("*.yaml")):
                with open(f) as fh:
                    config = yaml.safe_load(fh)
                    agent_type = config.get("agent_type", f.stem)
                    agents[agent_type] = config
            for f in sorted(self.agents_dir.glob("*.yml")):
                with open(f) as fh:
                    config = yaml.safe_load(fh)
                    agent_type = config.get("agent_type", f.stem)
                    agents[agent_type] = config
        return agents

    def discover_workflows(self) -> Dict[str, Workflow]:
        """Load workflows from workflows/ directory."""
        workflows = {}
        if self.workflows_dir.exists():
            for f in sorted(self.workflows_dir.glob("*.yaml")):
                try:
                    wf = Workflow.from_yaml(str(f))
                    workflows[wf.name] = wf
                except Exception as e:
                    print(f"  ⚠️  Skipping {f.name}: {e}")
            for f in sorted(self.workflows_dir.glob("*.yml")):
                try:
                    wf = Workflow.from_yaml(str(f))
                    workflows[wf.name] = wf
                except Exception as e:
                    print(f"  ⚠️  Skipping {f.name}: {e}")
        return workflows

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _process_job(self, session_factory, workflow_lookup):
        """Dequeue and process one job."""
        job = await self.queue.dequeue(timeout=0.5)
        if not job:
            return False

        async with session_factory() as db:
            if job.function == "process_step":
                task_id = job.kwargs["task_id"]
                step_id = job.kwargs["step_id"]

                orchestrator = Orchestrator(
                    task_id=task_id,
                    db=db,
                    agent_registry=self.agent_registry,
                )
                result = await orchestrator.execute_step(step_id)

                if result.error:
                    print(f"  ❌ Step {step_id}: {result.error}")
                else:
                    print(f"  ✅ Step {step_id} completed")

                # Advance the DAG
                from sqlalchemy import select as sa_select

                task_stmt = sa_select(Task).where(Task.id == task_id)
                task_result = await db.execute(task_stmt)
                task = task_result.scalar_one_or_none()

                if task and task.workflow_id in workflow_lookup:
                    workflow = workflow_lookup[task.workflow_id]
                    ready = await orchestrator.advance(workflow)

                    for step in ready:
                        await self.queue.enqueue_job(
                            "process_step",
                            step_id=step.id,
                            task_id=task_id,
                        )

                    if not ready and self.queue.empty:
                        # Check if task is done
                        task_stmt2 = sa_select(Task).where(Task.id == task_id)
                        task_result2 = await db.execute(task_stmt2)
                        task2 = task_result2.scalar_one_or_none()
                        if task2 and task2.status in ("completed", "failed"):
                            print(
                                f"\n🦠 Workflow '{task2.workflow_id}' "
                                f"{task2.status}!"
                            )
                            return "done"

            elif job.function == "start_workflow":
                await self._handle_start_workflow(db, job.kwargs)

        return True

    async def _handle_start_workflow(self, db, kwargs):
        """Handle a start_workflow job."""
        workflow_id = kwargs["workflow_id"]
        trigger_data = kwargs.get("trigger_data", {})

        task = Task(
            workflow_id=workflow_id,
            trigger_data=trigger_data,
        )
        db.add(task)
        await db.commit()

        workflow_path = self.workflows_dir / f"{workflow_id}.yaml"
        if not workflow_path.exists():
            workflow_path = self.workflows_dir / f"{workflow_id}.yml"

        if not workflow_path.exists():
            print(f"  ❌ Workflow '{workflow_id}' not found")
            return

        workflow = Workflow.from_yaml(str(workflow_path))
        orchestrator = Orchestrator(
            task_id=task.id,
            db=db,
            agent_registry=self.agent_registry,
        )
        await orchestrator.initialize_steps(workflow, trigger_data)

        # Enqueue root steps
        root_steps = await orchestrator._get_steps(status="pending")
        for step in root_steps:
            await self.queue.enqueue_job(
                "process_step",
                step_id=step.id,
                task_id=task.id,
            )

        print(
            f"  📋 Dispatched {len(root_steps)} root step(s) "
            f"for workflow '{workflow_id}'"
        )
        return task.id

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        workflow: Optional[str] = None,
        trigger: Optional[Dict[str, Any]] = None,
    ):
        """
        Start the embedded runner.

        Args:
            workflow: Optional workflow ID to start immediately
            trigger: Optional trigger data for the workflow
        """
        # Banner
        print()
        print("🦠 Microbe — Embedded Mode")
        print("─" * 40)

        # Discover
        self._agent_configs = self.discover_agents()
        self._workflows = self.discover_workflows()

        if not self._agent_configs:
            print("  ⚠️  No agents found in agents/ directory")
        else:
            print(f"  Agents:    {', '.join(self._agent_configs.keys())}")

        if not self._workflows:
            print("  ⚠️  No workflows found in workflows/ directory")
        else:
            print(f"  Workflows: {', '.join(self._workflows.keys())}")

        # Setup DB
        engine = create_engine(self.database_url)
        await init_db(engine)
        session_factory = create_session_factory(engine)
        print(f"  Database:  ready")
        print(f"  Queue:     in-memory")
        print("─" * 40)

        # Start workflow if requested
        if workflow:
            print(f"\n▶ Starting workflow: {workflow}")
            await self.queue.enqueue_job(
                "start_workflow",
                workflow_id=workflow,
                trigger_data=trigger or {},
            )

        # Handle shutdown
        def _signal_handler(sig, frame):
            print("\n\n🦠 Shutting down...")
            self._shutdown = True

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        # Main loop
        if workflow:
            print()
            while not self._shutdown:
                result = await self._process_job(
                    session_factory, self._workflows
                )
                if result == "done":
                    break
                if not result:
                    await asyncio.sleep(0.1)
        else:
            print("\n⏳ Waiting for workflows... (Ctrl+C to stop)")
            print(
                "   Use 'microbe run --workflow <name> --trigger '{...}'' "
                "to start one."
            )
            while not self._shutdown:
                await self._process_job(session_factory, self._workflows)

        # Cleanup
        await engine.dispose()
        print("🦠 Done.\n")


async def run_embedded(
    *,
    workflow: Optional[str] = None,
    trigger: Optional[Dict[str, Any]] = None,
    database_url: Optional[str] = None,
):
    """Convenience function to run embedded mode."""
    runner = EmbeddedRunner(database_url=database_url)
    await runner.run(workflow=workflow, trigger=trigger)
