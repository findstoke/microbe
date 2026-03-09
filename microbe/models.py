"""
Microbe Models — Generic Task + Step models.

These are Protocol-based so consumers can use their own ORM models
as long as they satisfy the interface.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel
from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel


# ---------------------------------------------------------------------------
# Protocols — consumers can bring their own models
# ---------------------------------------------------------------------------


@runtime_checkable
class TaskProtocol(Protocol):
    """Minimum interface the Orchestrator needs from a 'task' object."""

    id: str
    status: str
    workflow_id: str
    graph_state: dict | None


@runtime_checkable
class StepProtocol(Protocol):
    """Minimum interface the Orchestrator needs from a 'step' object."""

    id: str
    task_id: str
    agent_type: str
    status: str
    input_data: dict
    output_data: dict | None
    depends_on: list[str]


# ---------------------------------------------------------------------------
# Default SQLModel implementations (opt-in)
# ---------------------------------------------------------------------------


class Task(SQLModel, table=True):
    """
    A top-level unit of work. Tracks the execution of a full workflow.
    """

    __tablename__ = "microbe_tasks"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), primary_key=True
    )
    workflow_id: str = Field(index=True)
    status: str = Field(default="pending")
    # pending | running | completed | failed | cancelled

    # Trigger data — the initial input that started the workflow
    trigger_data: Optional[dict] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )

    # Shared memory across all steps
    graph_state: Optional[dict] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )

    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = Field(default=None)
    error: Optional[str] = Field(default=None, sa_type=Text)

    model_config = {"protected_namespaces": ()}


class Step(SQLModel, table=True):
    """
    One unit of work within a workflow. Maps to a single agent invocation.
    """

    __tablename__ = "microbe_steps"

    id: str = Field(
        default_factory=lambda: f"step_{uuid.uuid4().hex[:8]}",
        primary_key=True,
    )
    task_id: str = Field(foreign_key="microbe_tasks.id", index=True)

    # Identity
    step_def_id: str = Field(index=True)  # The 'id' from the workflow YAML
    agent_type: str = Field(index=True)  # Maps to agent_type in agent YAML
    description: Optional[str] = None

    # DAG control
    depends_on: List[str] = Field(default=[], sa_column=Column(JSON))
    foreach_index: Optional[int] = None  # For fan-out steps

    # Payloads
    input_data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    output_data: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    # Execution
    status: str = Field(default="pending")
    # pending | queued | running | completed | failed
    attempt: int = Field(default=0)
    max_attempts: int = Field(default=3)
    error_message: Optional[str] = Field(default=None, sa_type=Text)

    # Tracking
    model_used: Optional[str] = None
    token_usage: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    input_hash: Optional[str] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)

    # Sprouted steps — runtime DAG expansion
    spawned_by: Optional[str] = Field(default=None)  # Step ID that spawned this

    model_config = {"protected_namespaces": ()}
