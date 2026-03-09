"""
Microbe Orchestrator — The brain of the framework.

Reads a workflow YAML, dispatches steps to the shared Redis queue with
`agent_type` metadata, collects results, and advances the DAG —
including spawning new nodes at runtime based on agent output.
"""

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Type

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from microbe.agent import Agent, StepResult, UniversalAgent
from microbe.models import Step, Task
from microbe.workflow import Workflow, WorkflowStep, resolve_template


class Orchestrator:
    """
    Manages the lifecycle of a workflow execution.

    - Dispatches steps to agent workers via the shared Redis queue
    - Handles foreach fan-out (spawning N steps from a list)
    - Handles runtime DAG expansion (agents spawning new steps)
    - Advances the DAG when steps complete
    """

    # Cost control limits
    MAX_STEPS_PER_TASK = 50
    MAX_FAN_OUT = 20

    def __init__(
        self,
        task_id: str,
        db: AsyncSession,
        agent_registry: Optional[Dict[str, Type[Agent]]] = None,
    ):
        self.task_id = task_id
        self.db = db
        self.agent_registry = agent_registry or {}

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    async def _get_task(self) -> Optional[Task]:
        stmt = select(Task).where(Task.id == self.task_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_steps(
        self, status: Optional[str] = None
    ) -> List[Step]:
        stmt = select(Step).where(Step.task_id == self.task_id)
        if status:
            stmt = stmt.where(Step.status == status)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Step creation
    # ------------------------------------------------------------------

    def _fingerprint(self, agent_type: str, input_data: dict) -> str:
        """Deterministic hash for idempotency."""
        payload = f"{agent_type}:{json.dumps(input_data, sort_keys=True)}"
        return hashlib.sha256(payload.encode()).hexdigest()

    async def _create_step(
        self,
        step_def_id: str,
        agent_type: str,
        input_data: dict,
        depends_on: Optional[List[str]] = None,
        description: str = "",
        foreach_index: Optional[int] = None,
        spawned_by: Optional[str] = None,
    ) -> Optional[Step]:
        """Create a step if it doesn't already exist (idempotent)."""
        fingerprint = self._fingerprint(agent_type, input_data)

        # Check for duplicate
        stmt = select(Step).where(
            and_(
                Step.task_id == self.task_id,
                Step.input_hash == fingerprint,
            )
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        step = Step(
            task_id=self.task_id,
            step_def_id=step_def_id,
            agent_type=agent_type,
            description=description,
            input_data=input_data,
            depends_on=depends_on or [],
            foreach_index=foreach_index,
            spawned_by=spawned_by,
            input_hash=fingerprint,
            status="pending",
        )
        self.db.add(step)
        await self.db.flush()
        return step

    async def initialize_steps(self, workflow: Workflow, trigger_data: dict):
        """
        Create initial Steps from the workflow definition.

        Only creates steps that have NO dependencies (root steps).
        Other steps are created when their dependencies complete.
        """
        task = await self._get_task()
        if not task:
            raise ValueError(f"Task {self.task_id} not found")

        task.trigger_data = trigger_data
        task.status = "running"

        root_steps = workflow.get_ready_steps(completed_ids=set())

        for ws in root_steps:
            input_data = resolve_template(
                ws.input,
                {"trigger": trigger_data, "steps": {}, "env": {}},
            )

            if ws.foreach:
                # Fan-out: resolve the foreach expression
                items = resolve_template(
                    ws.foreach,
                    {"trigger": trigger_data, "steps": {}, "env": {}},
                )
                if isinstance(items, list):
                    for i, item in enumerate(items[: self.MAX_FAN_OUT]):
                        item_input = resolve_template(
                            ws.input,
                            {
                                "trigger": trigger_data,
                                "steps": {},
                                "item": item,
                                "env": {},
                            },
                        )
                        await self._create_step(
                            step_def_id=ws.id,
                            agent_type=ws.agent,
                            input_data=item_input,
                            depends_on=[],
                            description=ws.description,
                            foreach_index=i,
                        )
            else:
                await self._create_step(
                    step_def_id=ws.id,
                    agent_type=ws.agent,
                    input_data=input_data,
                    depends_on=[],
                    description=ws.description,
                )

        await self.db.commit()

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    def _resolve_agent(self, agent_type: str) -> Agent:
        """Resolve an agent type to an Agent instance."""
        if agent_type in self.agent_registry:
            return self.agent_registry[agent_type](agent_type=agent_type)
        return UniversalAgent(agent_type=agent_type)

    async def execute_step(self, step_id: str) -> StepResult:
        """
        Execute a single step. Called by agent workers.

        This is the method that runs inside each Arq worker process.
        """
        stmt = select(Step).where(Step.id == step_id)
        result = await self.db.execute(stmt)
        step = result.scalar_one_or_none()

        if not step:
            return StepResult(data={}, error=f"Step {step_id} not found")

        # Get task for graph state
        task = await self._get_task()
        if not task:
            return StepResult(data={}, error=f"Task {self.task_id} not found")

        # Update status
        step.status = "running"
        step.started_at = datetime.utcnow()
        step.attempt += 1
        await self.db.commit()

        # Resolve and execute agent
        agent = self._resolve_agent(step.agent_type)

        try:
            step_result = await agent.execute(
                input_data=step.input_data,
                context=task.graph_state or {},
            )

            if step_result.error:
                step.status = "failed"
                step.error_message = step_result.error
            else:
                step.status = "completed"
                step.output_data = step_result.data
                step.completed_at = datetime.utcnow()
                step.model_used = agent.config.get("model")
                step.token_usage = step_result.token_usage

                # Handle runtime DAG expansion
                if step_result.spawn:
                    await self._handle_spawn(step, step_result.spawn)

        except Exception as e:
            step.status = "failed"
            step.error_message = str(e)
            step_result = StepResult(data={}, error=str(e))

        await self.db.commit()
        return step_result

    # ------------------------------------------------------------------
    # Runtime DAG expansion
    # ------------------------------------------------------------------

    async def _handle_spawn(self, parent_step: Step, spawn_list: list):
        """
        Handle runtime DAG expansion.

        Agents can return a `spawn` list in their StepResult to create
        new steps that weren't in the original workflow.
        """
        total_steps = len(await self._get_steps())

        for entry in spawn_list:
            if total_steps >= self.MAX_STEPS_PER_TASK:
                print(
                    f"⚠️  [Microbe] Max steps ({self.MAX_STEPS_PER_TASK}) "
                    "reached. Stopping spawn."
                )
                break

            await self._create_step(
                step_def_id=entry.get("id", f"spawned_{parent_step.id}"),
                agent_type=entry.get("agent", entry.get("agent_type", "universal")),
                input_data=entry.get("input", entry.get("inputData", {})),
                depends_on=[parent_step.id],
                description=entry.get("description", "Runtime-spawned step"),
                spawned_by=parent_step.id,
            )
            total_steps += 1

    # ------------------------------------------------------------------
    # DAG advancement
    # ------------------------------------------------------------------

    async def advance(self, workflow: Workflow) -> List[Step]:
        """
        Check the DAG and create/dispatch any newly-ready steps.

        Returns a list of steps that are ready to run.

        Call this after a step completes to progress the workflow.
        """
        task = await self._get_task()
        if not task:
            return []

        all_steps = await self._get_steps()
        completed_step_ids = {
            s.id for s in all_steps if s.status == "completed"
        }
        completed_def_ids = {
            s.step_def_id for s in all_steps if s.status == "completed"
        }
        existing_def_ids = {s.step_def_id for s in all_steps}

        # Build step outputs for template resolution
        step_outputs: Dict[str, Any] = {}
        for s in all_steps:
            if s.status == "completed" and s.output_data:
                if s.step_def_id not in step_outputs:
                    step_outputs[s.step_def_id] = {"output": s.output_data}
                else:
                    # Fan-in: merge outputs from foreach steps
                    existing = step_outputs[s.step_def_id]["output"]
                    if not isinstance(existing, list):
                        step_outputs[s.step_def_id]["output"] = [existing]
                    step_outputs[s.step_def_id]["output"].append(s.output_data)

        # Check for newly unblocked workflow steps
        ready_steps = []

        for ws in workflow.steps:
            if ws.id in existing_def_ids:
                continue  # Already created

            # Check if all dependencies are completed
            deps_met = all(dep in completed_def_ids for dep in ws.depends_on)
            if not deps_met:
                continue

            # Resolve input templates
            context = {
                "trigger": task.trigger_data or {},
                "steps": step_outputs,
                "env": {},
            }

            if ws.foreach:
                # Fan-out
                items = resolve_template(ws.foreach, context)
                if isinstance(items, list):
                    for i, item in enumerate(items[: self.MAX_FAN_OUT]):
                        item_context = {**context, "item": item}
                        input_data = resolve_template(ws.input, item_context)
                        step = await self._create_step(
                            step_def_id=ws.id,
                            agent_type=ws.agent,
                            input_data=input_data,
                            depends_on=list(completed_step_ids),
                            description=ws.description,
                            foreach_index=i,
                        )
                        if step and step.status == "pending":
                            ready_steps.append(step)
            else:
                input_data = resolve_template(ws.input, context)
                step = await self._create_step(
                    step_def_id=ws.id,
                    agent_type=ws.agent,
                    input_data=input_data,
                    depends_on=list(completed_step_ids),
                    description=ws.description,
                )
                if step and step.status == "pending":
                    ready_steps.append(step)

        # Also check spawned steps that are pending
        pending_spawned = [
            s
            for s in all_steps
            if s.status == "pending"
            and s.spawned_by
            and s.spawned_by in completed_step_ids
        ]
        ready_steps.extend(pending_spawned)

        # Check if workflow is complete
        all_current = await self._get_steps()
        if all_current and all(
            s.status in ("completed", "failed") for s in all_current
        ):
            failed = [s for s in all_current if s.status == "failed"]
            task.status = "failed" if failed else "completed"
            task.completed_at = datetime.utcnow()
            if failed:
                task.error = f"{len(failed)} step(s) failed"

        await self.db.commit()
        return ready_steps
