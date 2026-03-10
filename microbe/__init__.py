"""
Microbe — Microservices principles applied to AI agents.

Each agent is an independently deployable worker.
Workflows are human-readable YAML DAGs that non-technical users can edit.
"""

from microbe.agent import Agent, StepResult, UniversalAgent
from microbe.orchestrator import Orchestrator
from microbe.workflow import Workflow
from microbe.llm import LLMProvider, LLMProviderRegistry, provider_registry
from microbe.queue import InMemoryQueue
from microbe.runner import EmbeddedRunner

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "StepResult",
    "UniversalAgent",
    "Orchestrator",
    "Workflow",
    "LLMProvider",
    "LLMProviderRegistry",
    "provider_registry",
    "InMemoryQueue",
    "EmbeddedRunner",
]
