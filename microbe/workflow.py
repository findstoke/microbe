"""
Microbe Workflow — YAML workflow parser and DAG resolver.

Parses human-readable YAML workflow files into executable DAG structures.
Handles template expressions, foreach fan-out, and dependency resolution.
"""

import re
from typing import Any, Dict, List, Optional

import yaml


class WorkflowStep:
    """Parsed representation of a single step in a workflow."""

    def __init__(
        self,
        id: str,
        agent: str,
        description: str = "",
        depends_on: Optional[List[str]] = None,
        foreach: Optional[str] = None,
        input: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        self.id = id
        self.agent = agent
        self.description = description
        self.depends_on = depends_on or []
        self.foreach = foreach
        self.input = input or {}
        self.extra = kwargs

    def __repr__(self):
        deps = f" depends_on={self.depends_on}" if self.depends_on else ""
        fan = " [fan-out]" if self.foreach else ""
        return f"<Step '{self.id}' agent={self.agent}{deps}{fan}>"


class Workflow:
    """
    Parsed workflow from a YAML file.

    Provides DAG resolution, topological ordering, and template evaluation.
    """

    def __init__(self, name: str, description: str, steps: List[WorkflowStep]):
        self.name = name
        self.description = description
        self.steps = steps
        self._step_map = {s.id: s for s in steps}
        self._validate()

    def _validate(self):
        """Validate the workflow DAG for basic correctness."""
        ids = set(self._step_map.keys())

        for step in self.steps:
            for dep in step.depends_on:
                if dep not in ids:
                    raise ValueError(
                        f"Step '{step.id}' depends on '{dep}', "
                        f"which does not exist. Available: {ids}"
                    )

        # Check for cycles
        visited = set()
        path = set()

        def _dfs(step_id: str):
            if step_id in path:
                raise ValueError(
                    f"Circular dependency detected involving '{step_id}'"
                )
            if step_id in visited:
                return
            path.add(step_id)
            for dep in self._step_map[step_id].depends_on:
                _dfs(dep)
            path.discard(step_id)
            visited.add(step_id)

        for step_id in ids:
            _dfs(step_id)

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        return self._step_map.get(step_id)

    def get_ready_steps(self, completed_ids: set) -> List[WorkflowStep]:
        """
        Get steps whose dependencies are all satisfied.
        Used by the orchestrator to determine what to dispatch next.
        """
        ready = []
        for step in self.steps:
            if step.id in completed_ids:
                continue
            if all(dep in completed_ids for dep in step.depends_on):
                ready.append(step)
        return ready

    def topological_order(self) -> List[WorkflowStep]:
        """Return steps in a valid execution order."""
        result = []
        visited = set()

        def _visit(step_id: str):
            if step_id in visited:
                return
            for dep in self._step_map[step_id].depends_on:
                _visit(dep)
            visited.add(step_id)
            result.append(self._step_map[step_id])

        for step in self.steps:
            _visit(step.id)

        return result

    @classmethod
    def from_yaml(cls, path: str) -> "Workflow":
        """Parse a workflow from a YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Workflow":
        """Parse a workflow from a dictionary."""
        steps = []
        for step_data in data.get("steps", []):
            steps.append(
                WorkflowStep(
                    id=step_data["id"],
                    agent=step_data["agent"],
                    description=step_data.get("description", ""),
                    depends_on=step_data.get("depends_on"),
                    foreach=step_data.get("foreach"),
                    input=step_data.get("input", {}),
                )
            )

        return cls(
            name=data.get("name", "unnamed"),
            description=data.get("description", ""),
            steps=steps,
        )


# ---------------------------------------------------------------------------
# Template Expression Engine
# ---------------------------------------------------------------------------

# Matches {{ steps.plan.output.queries }} or {{ trigger.query }} etc.
TEMPLATE_PATTERN = re.compile(r"\{\{\s*(.+?)\s*\}\}")


def resolve_template(
    value: Any,
    context: Dict[str, Any],
) -> Any:
    """
    Resolve template expressions in a value.

    Supports:
        {{ trigger.query }}       — trigger data
        {{ steps.plan.output }}   — prior step output
        {{ steps.plan.output.queries }} — nested access
        {{ steps.analyze.output.* }}    — collect all outputs (fan-in)
        {{ item }}                — current foreach item
        {{ env.API_KEY }}         — environment variable

    Args:
        value: The value to resolve (string, dict, list, or primitive)
        context: Resolution context with keys like 'trigger', 'steps', 'item', 'env'
    """
    if isinstance(value, str):
        # Check if the entire string is a single expression
        match = TEMPLATE_PATTERN.fullmatch(value.strip())
        if match:
            # Full expression — resolve to native type (not string)
            return _resolve_path(match.group(1).strip(), context)

        # Multiple expressions or mixed — string interpolation
        def _replace(m):
            resolved = _resolve_path(m.group(1).strip(), context)
            return str(resolved) if resolved is not None else ""

        return TEMPLATE_PATTERN.sub(_replace, value)

    elif isinstance(value, dict):
        return {k: resolve_template(v, context) for k, v in value.items()}

    elif isinstance(value, list):
        return [resolve_template(item, context) for item in value]

    return value


def _resolve_path(path: str, context: Dict[str, Any]) -> Any:
    """
    Resolve a dot-separated path against the context.

    Special case: 'steps.*.output' or 'steps.search.output.*'
    collects all matching values into a list (fan-in).
    """
    parts = path.split(".")
    current = context

    for i, part in enumerate(parts):
        if part == "*":
            # Wildcard — collect all values at this level
            if isinstance(current, dict):
                remaining = ".".join(parts[i + 1 :])
                if remaining:
                    results = []
                    for v in current.values():
                        resolved = _resolve_path(remaining, {"_root": v})
                        if resolved is not None:
                            if isinstance(resolved, list):
                                results.extend(resolved)
                            else:
                                results.append(resolved)
                    return results
                return list(current.values())
            elif isinstance(current, list):
                remaining = ".".join(parts[i + 1 :])
                if remaining:
                    results = []
                    for v in current:
                        resolved = _resolve_path(remaining, {"_root": v})
                        if resolved is not None:
                            if isinstance(resolved, list):
                                results.extend(resolved)
                            else:
                                results.append(resolved)
                    return results
                return current
            return None

        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None

        if current is None:
            return None

    return current
