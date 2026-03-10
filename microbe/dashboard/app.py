"""
Microbe Dashboard — FastAPI application.

REST + WebSocket API with HTMX-powered Jinja2 templates.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func

from microbe.db import create_engine, create_session_factory, init_db
from microbe.models import Step, Task
from microbe.workflow import Workflow

DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"


def create_app(
    *,
    database_url: Optional[str] = None,
    workflows_dir: str = "workflows",
    agents_dir: str = "agents",
) -> FastAPI:
    """Create the dashboard FastAPI application."""

    app = FastAPI(
        title="Microbe Dashboard",
        description="Real-time workflow monitoring",
        version="0.1.0",
    )

    # Static files and templates
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Database
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)

    # Workflow discovery
    wf_dir = Path(workflows_dir)
    ag_dir = Path(agents_dir)

    # ------------------------------------------------------------------
    # HTML Pages
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Task list page."""
        async with session_factory() as db:
            stmt = select(Task).order_by(Task.created_at.desc()).limit(50)
            result = await db.execute(stmt)
            tasks = list(result.scalars().all())

            # Get step counts per task
            task_stats = {}
            for task in tasks:
                steps_stmt = select(Step).where(Step.task_id == task.id)
                steps_result = await db.execute(steps_stmt)
                steps = list(steps_result.scalars().all())
                total = len(steps)
                completed = len(
                    [s for s in steps if s.status == "completed"]
                )
                failed = len([s for s in steps if s.status == "failed"])
                running = len([s for s in steps if s.status == "running"])
                task_stats[task.id] = {
                    "total": total,
                    "completed": completed,
                    "failed": failed,
                    "running": running,
                }

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "tasks": tasks,
                "task_stats": task_stats,
            },
        )

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    async def task_detail(request: Request, task_id: str):
        """DAG visualization page for a specific task."""
        async with session_factory() as db:
            task_stmt = select(Task).where(Task.id == task_id)
            task_result = await db.execute(task_stmt)
            task = task_result.scalar_one_or_none()

            if not task:
                return HTMLResponse("Task not found", status_code=404)

            steps_stmt = (
                select(Step)
                .where(Step.task_id == task_id)
                .order_by(Step.created_at)
            )
            steps_result = await db.execute(steps_stmt)
            steps = list(steps_result.scalars().all())

        # Build DAG layers for visualization
        dag_layers = _build_dag_layers(steps)

        return templates.TemplateResponse(
            "task_detail.html",
            {
                "request": request,
                "task": task,
                "steps": steps,
                "dag_layers": dag_layers,
            },
        )

    @app.get("/workflows", response_class=HTMLResponse)
    async def workflows_page(request: Request):
        """Browse discovered workflows."""
        workflows = _discover_workflows(wf_dir)
        agents = _discover_agents(ag_dir)
        return templates.TemplateResponse(
            "workflows.html",
            {
                "request": request,
                "workflows": workflows,
                "agents": agents,
            },
        )

    @app.get("/run", response_class=HTMLResponse)
    async def run_page(request: Request):
        """Form to start a new workflow."""
        workflows = _discover_workflows(wf_dir)
        return templates.TemplateResponse(
            "run.html",
            {"request": request, "workflows": workflows},
        )

    # ------------------------------------------------------------------
    # HTMX Partials
    # ------------------------------------------------------------------

    @app.get("/partials/task-list", response_class=HTMLResponse)
    async def partial_task_list(request: Request):
        """Partial for HTMX polling — task list cards."""
        async with session_factory() as db:
            stmt = select(Task).order_by(Task.created_at.desc()).limit(50)
            result = await db.execute(stmt)
            tasks = list(result.scalars().all())

            task_stats = {}
            for task in tasks:
                steps_stmt = select(Step).where(Step.task_id == task.id)
                steps_result = await db.execute(steps_stmt)
                steps = list(steps_result.scalars().all())
                total = len(steps)
                completed = len(
                    [s for s in steps if s.status == "completed"]
                )
                failed = len([s for s in steps if s.status == "failed"])
                running = len([s for s in steps if s.status == "running"])
                task_stats[task.id] = {
                    "total": total,
                    "completed": completed,
                    "failed": failed,
                    "running": running,
                }

        return templates.TemplateResponse(
            "partials/task_list.html",
            {
                "request": request,
                "tasks": tasks,
                "task_stats": task_stats,
            },
        )

    @app.get("/partials/dag/{task_id}", response_class=HTMLResponse)
    async def partial_dag(request: Request, task_id: str):
        """Partial for HTMX polling — DAG visualization."""
        async with session_factory() as db:
            task_stmt = select(Task).where(Task.id == task_id)
            task_result = await db.execute(task_stmt)
            task = task_result.scalar_one_or_none()

            steps_stmt = (
                select(Step)
                .where(Step.task_id == task_id)
                .order_by(Step.created_at)
            )
            steps_result = await db.execute(steps_stmt)
            steps = list(steps_result.scalars().all())

        dag_layers = _build_dag_layers(steps)

        return templates.TemplateResponse(
            "partials/dag.html",
            {
                "request": request,
                "task": task,
                "steps": steps,
                "dag_layers": dag_layers,
            },
        )

    # ------------------------------------------------------------------
    # REST API
    # ------------------------------------------------------------------

    @app.get("/api/tasks")
    async def api_list_tasks():
        async with session_factory() as db:
            stmt = select(Task).order_by(Task.created_at.desc()).limit(50)
            result = await db.execute(stmt)
            tasks = list(result.scalars().all())
            return [
                {
                    "id": t.id,
                    "workflow_id": t.workflow_id,
                    "status": t.status,
                    "created_at": t.created_at.isoformat()
                    if t.created_at
                    else None,
                    "completed_at": t.completed_at.isoformat()
                    if t.completed_at
                    else None,
                }
                for t in tasks
            ]

    @app.get("/api/tasks/{task_id}")
    async def api_get_task(task_id: str):
        async with session_factory() as db:
            task_stmt = select(Task).where(Task.id == task_id)
            task_result = await db.execute(task_stmt)
            task = task_result.scalar_one_or_none()
            if not task:
                return {"error": "Task not found"}

            steps_stmt = select(Step).where(Step.task_id == task_id)
            steps_result = await db.execute(steps_stmt)
            steps = list(steps_result.scalars().all())

            return {
                "id": task.id,
                "workflow_id": task.workflow_id,
                "status": task.status,
                "trigger_data": task.trigger_data,
                "created_at": task.created_at.isoformat()
                if task.created_at
                else None,
                "completed_at": task.completed_at.isoformat()
                if task.completed_at
                else None,
                "steps": [
                    {
                        "id": s.id,
                        "step_def_id": s.step_def_id,
                        "agent_type": s.agent_type,
                        "status": s.status,
                        "depends_on": s.depends_on,
                        "foreach_index": s.foreach_index,
                        "spawned_by": s.spawned_by,
                        "created_at": s.created_at.isoformat()
                        if s.created_at
                        else None,
                        "completed_at": s.completed_at.isoformat()
                        if s.completed_at
                        else None,
                        "error_message": s.error_message,
                        "token_usage": s.token_usage,
                    }
                    for s in steps
                ],
            }

    @app.post("/api/run")
    async def api_run_workflow(request: Request):
        """Start a new workflow run from the dashboard form."""
        import uuid
        from datetime import datetime, timezone

        form = await request.form()
        workflow_name = form.get("workflow", "")
        trigger_raw = form.get("trigger", "{}")

        try:
            trigger_data = json.loads(trigger_raw) if trigger_raw else {}
        except json.JSONDecodeError:
            trigger_data = {}

        # Find the workflow definition
        workflows = _discover_workflows(wf_dir)
        wf_def = next(
            (w for w in workflows if w["name"] == workflow_name), None
        )

        if not wf_def:
            return HTMLResponse(
                f"Workflow '{workflow_name}' not found", status_code=404
            )

        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        async with session_factory() as db:
            # Create task
            task = Task(
                id=task_id,
                workflow_id=workflow_name,
                status="running",
                trigger_data=trigger_data,
                graph={},
                created_at=now,
            )
            db.add(task)

            # Create initial steps from workflow definition
            step_ids = {}
            for step_def in wf_def["steps"]:
                step_id = str(uuid.uuid4())
                step_ids[step_def["id"]] = step_id

            for step_def in wf_def["steps"]:
                dep_ids = [
                    step_ids[d]
                    for d in (step_def.get("depends_on") or [])
                    if d in step_ids
                ]
                step = Step(
                    id=step_ids[step_def["id"]],
                    task_id=task_id,
                    step_def_id=step_def["id"],
                    agent_type=step_def["agent"],
                    status="pending",
                    depends_on=dep_ids,
                    input_data=trigger_data,
                    created_at=now,
                )
                db.add(step)

            await db.commit()

        # Redirect to the task detail page
        from fastapi.responses import RedirectResponse

        return RedirectResponse(
            url=f"/tasks/{task_id}", status_code=303
        )

    @app.get("/api/tasks/{task_id}/dag")
    async def api_get_dag(task_id: str):
        async with session_factory() as db:
            steps_stmt = (
                select(Step)
                .where(Step.task_id == task_id)
                .order_by(Step.created_at)
            )
            steps_result = await db.execute(steps_stmt)
            steps = list(steps_result.scalars().all())

        return _build_dag_layers(steps)

    # ------------------------------------------------------------------
    # WebSocket for real-time updates
    # ------------------------------------------------------------------

    @app.websocket("/ws/tasks/{task_id}")
    async def ws_task_updates(websocket: WebSocket, task_id: str):
        await websocket.accept()
        last_state = {}

        try:
            while True:
                async with session_factory() as db:
                    steps_stmt = select(Step).where(Step.task_id == task_id)
                    steps_result = await db.execute(steps_stmt)
                    steps = list(steps_result.scalars().all())

                    current_state = {
                        s.id: {
                            "status": s.status,
                            "step_def_id": s.step_def_id,
                            "agent_type": s.agent_type,
                        }
                        for s in steps
                    }

                    if current_state != last_state:
                        last_state = current_state
                        await websocket.send_json(
                            {
                                "type": "dag_update",
                                "steps": current_state,
                            }
                        )

                await asyncio.sleep(1)
        except WebSocketDisconnect:
            pass

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    @app.on_event("startup")
    async def on_startup():
        await init_db(engine)

    return app


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _build_dag_layers(steps: list) -> list:
    """
    Organize steps into layers for vertical DAG visualization.

    Returns a list of layers, each layer is a list of step dicts.
    Steps in the same layer can run in parallel.
    """
    if not steps:
        return []

    # Group by step_def_id and build dependency graph
    step_groups = {}
    for s in steps:
        key = s.step_def_id
        if key not in step_groups:
            step_groups[key] = {
                "step_def_id": key,
                "agent_type": s.agent_type,
                "description": s.description,
                "depends_on_defs": set(),
                "steps": [],
            }
        step_groups[key]["steps"].append(
            {
                "id": s.id,
                "status": s.status,
                "foreach_index": s.foreach_index,
                "spawned_by": s.spawned_by,
                "error_message": s.error_message,
                "token_usage": s.token_usage,
                "created_at": s.created_at.isoformat()
                if s.created_at
                else None,
                "started_at": s.started_at.isoformat()
                if s.started_at
                else None,
                "completed_at": s.completed_at.isoformat()
                if s.completed_at
                else None,
            }
        )

    # Resolve depends_on from step IDs to step_def_ids
    step_id_to_def = {s.id: s.step_def_id for s in steps}
    for s in steps:
        key = s.step_def_id
        for dep_id in s.depends_on or []:
            dep_def = step_id_to_def.get(dep_id)
            if dep_def and dep_def != key:
                step_groups[key]["depends_on_defs"].add(dep_def)

    # Topological sort into layers
    layers = []
    placed = set()

    while len(placed) < len(step_groups):
        layer = []
        for key, group in step_groups.items():
            if key in placed:
                continue
            if all(dep in placed for dep in group["depends_on_defs"]):
                layer.append(
                    {
                        "step_def_id": group["step_def_id"],
                        "agent_type": group["agent_type"],
                        "description": group["description"],
                        "steps": group["steps"],
                        "is_fan_out": len(group["steps"]) > 1,
                    }
                )
        if not layer:
            break  # Avoid infinite loop on circular deps
        for item in layer:
            placed.add(item["step_def_id"])
        layers.append(layer)

    return layers


def _discover_workflows(wf_dir: Path) -> list:
    """Discover workflow YAML files."""
    workflows = []
    if wf_dir.exists():
        for f in sorted(wf_dir.glob("*.yaml")) + sorted(
            wf_dir.glob("*.yml")
        ):
            try:
                wf = Workflow.from_yaml(str(f))
                workflows.append(
                    {
                        "name": wf.name,
                        "description": wf.description,
                        "steps": [
                            {
                                "id": s.id,
                                "agent": s.agent,
                                "depends_on": s.depends_on,
                                "foreach": s.foreach,
                            }
                            for s in wf.steps
                        ],
                    }
                )
            except Exception:
                pass
    return workflows


def _discover_agents(ag_dir: Path) -> list:
    """Discover agent YAML files."""
    import yaml

    agents = []
    if ag_dir.exists():
        for f in sorted(ag_dir.glob("*.yaml")) + sorted(
            ag_dir.glob("*.yml")
        ):
            try:
                with open(f) as fh:
                    config = yaml.safe_load(fh)
                agents.append(
                    {
                        "name": config.get("name", f.stem),
                        "agent_type": config.get("agent_type", f.stem),
                        "description": config.get("description", ""),
                        "model": config.get("model", ""),
                        "provider": config.get("provider", ""),
                    }
                )
            except Exception:
                pass
    return agents
