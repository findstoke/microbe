"""
Microbe Agent — Base class for all agents.

Agents are single-purpose workers defined by a YAML spec + optional Python class.
If no custom class is provided, UniversalAgent handles execution via LLM.
"""

import json
import os
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel

from microbe.llm import provider_registry


class StepResult(BaseModel):
    """Result returned by an agent after executing a step."""

    data: Dict[str, Any]
    error: Optional[str] = None
    token_usage: Optional[Dict[str, Any]] = None
    spawn: Optional[list] = None  # Runtime DAG expansion — new steps to add


class Agent:
    """
    Base class for Microbe agents.

    An agent is a single-purpose worker. Override `execute()` to implement
    custom logic, or use UniversalAgent for pure LLM-based execution.
    """

    def __init__(self, agent_type: str, config_path: Optional[str] = None):
        self.agent_type = agent_type
        self.config: Dict[str, Any] = {}

        if config_path:
            self.config = self._load_config(config_path)
        elif agent_type:
            # Try auto-discovering config from standard locations
            self.config = self._discover_config(agent_type)

    def _load_config(self, path: str) -> Dict[str, Any]:
        """Load agent configuration from a YAML file."""
        if not os.path.exists(path):
            raise FileNotFoundError(f"Agent config not found: {path}")
        with open(path, "r") as f:
            return yaml.safe_load(f) or {}

    def _discover_config(self, agent_type: str) -> Dict[str, Any]:
        """Try to find the agent YAML in standard locations."""
        search_paths = [
            f"agents/{agent_type}.yaml",
            f"agents/{agent_type}.yml",
            f"registry/{agent_type}.yaml",
        ]
        for path in search_paths:
            if os.path.exists(path):
                return self._load_config(path)
        return {}

    async def execute(
        self,
        input_data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> StepResult:
        """
        Core execution logic. Override this for custom behavior.

        Args:
            input_data: Step-specific input from the workflow
            context: Shared graph state across all steps

        Returns:
            StepResult with output data, optional token_usage, and optional
            spawn list for runtime DAG expansion.
        """
        raise NotImplementedError(
            f"Agent '{self.agent_type}' must implement execute() "
            "or use UniversalAgent."
        )


class UniversalAgent(Agent):
    """
    Generic agent that handles any YAML-defined task using LLMs.
    Reads system_prompt, model, and response_format from the YAML spec
    and executes an LLM call.
    """

    async def execute(
        self,
        input_data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> StepResult:
        system_prompt = self.config.get(
            "system_prompt", "You are a helpful assistant."
        )
        model = self.config.get("model", "gpt-4o-mini")
        temperature = self.config.get("temperature", 0.3)
        max_tokens = self.config.get("max_tokens", 2000)

        user_content = (
            f"INPUT: {json.dumps(input_data)}\n\n"
            f"CONTEXT: {json.dumps(context)}"
        )

        # Resolve provider
        provider_name = self.config.get("provider")
        provider = provider_registry.get_provider(
            model, requested_provider=provider_name
        )

        if not provider:
            return StepResult(
                data={},
                error=f"No LLM provider found for model '{model}'. "
                f"Available: {provider_registry.available_providers}",
            )

        try:
            response_format = None
            if self.config.get("response_format") == "json":
                response_format = {"type": "json_object"}

            llm_response = await provider.generate_completion(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )

            content = llm_response.content

            # Parse JSON response
            if self.config.get("response_format") == "json":
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    # One repair attempt
                    repair_response = await provider.generate_completion(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are a JSON repair tool. "
                                    "Return ONLY a valid JSON object."
                                ),
                            },
                            {"role": "user", "content": content},
                        ],
                        temperature=0,
                        max_tokens=max_tokens,
                        response_format={"type": "json_object"},
                    )
                    data = json.loads(repair_response.content)
            else:
                data = {"content": content}

            # Check for runtime DAG expansion
            spawn = data.pop("spawn", None) or data.pop("next_steps", None)

            return StepResult(
                data=data,
                token_usage=llm_response.token_usage,
                spawn=spawn if isinstance(spawn, list) else None,
            )

        except Exception as e:
            return StepResult(data={}, error=f"LLM Error: {str(e)}")
