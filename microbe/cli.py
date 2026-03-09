"""
Microbe CLI — Project scaffolding and local execution.

Commands:
    microbe init <name>        — Scaffold a new project
    microbe new-agent <name>   — Add an agent to the current project
    microbe run                — Start orchestrator + agent workers
    microbe run --agent <name> — Start a specific agent worker
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _render_template(
    template_name: str,
    output_path: Path,
    context: dict,
):
    """Render a Jinja2 template to a file."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        keep_trailing_newline=True,
    )
    template = env.get_template(template_name)
    content = template.render(**context)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)


def _copy_template_file(template_name: str, output_path: Path):
    """Copy a non-template file as-is."""
    src = TEMPLATES_DIR / template_name
    if src.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, output_path)


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Microbe — Microservices principles applied to AI agents."""
    pass


@cli.command()
@click.argument("name")
def init(name: str):
    """Scaffold a new Microbe project."""
    project_dir = Path(name)

    if project_dir.exists():
        click.echo(f"Error: Directory '{name}' already exists.", err=True)
        sys.exit(1)

    click.echo(f"🦠 Creating Microbe project: {name}")

    ctx = {"project_name": name}

    # Core files
    _render_template(
        "project/worker.py.j2", project_dir / "worker.py", ctx
    )
    _render_template(
        "project/docker-compose.yml.j2",
        project_dir / "docker-compose.yml",
        ctx,
    )
    _render_template(
        "project/env_example.j2", project_dir / ".env.example", ctx
    )
    _render_template(
        "project/requirements.txt.j2",
        project_dir / "requirements.txt",
        ctx,
    )

    # Example workflow
    _copy_template_file(
        "project/workflows/example.yaml",
        project_dir / "workflows" / "example.yaml",
    )

    # Example agent
    _render_template(
        "agent.yaml.j2",
        project_dir / "agents" / "example.yaml",
        {
            "agent_name": "example",
            "agent_type": "example",
            "description": "An example agent that summarizes input text",
            "model": "gpt-4o-mini",
            "provider": "openai",
        },
    )

    # Gitignore
    (project_dir / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\nvenv/\n.venv/\n.env\n"
    )

    click.echo(f"\n✅ Project created at ./{name}/")
    click.echo(f"\nNext steps:")
    click.echo(f"  cd {name}")
    click.echo(f"  cp .env.example .env")
    click.echo(f"  docker compose up -d")
    click.echo(f"  pip install microbe")
    click.echo(f"  microbe run")


@cli.command("new-agent")
@click.argument("name")
def new_agent(name: str):
    """Add a new agent definition to the current project."""
    agents_dir = Path("agents")

    if not agents_dir.exists():
        click.echo(
            "Error: No 'agents/' directory found. "
            "Are you in a Microbe project?",
            err=True,
        )
        sys.exit(1)

    output_path = agents_dir / f"{name}.yaml"
    if output_path.exists():
        click.echo(f"Error: Agent '{name}' already exists.", err=True)
        sys.exit(1)

    _render_template(
        "agent.yaml.j2",
        output_path,
        {
            "agent_name": name,
            "agent_type": name,
            "description": f"Agent: {name}",
            "model": "gpt-4o-mini",
            "provider": "openai",
        },
    )

    click.echo(f"🦠 Created agent: agents/{name}.yaml")


@cli.command()
@click.option(
    "--agent",
    default=None,
    help="Run a specific agent worker only.",
)
def run(agent: Optional[str]):
    """Start the orchestrator and/or agent workers."""
    if agent:
        click.echo(f"🦠 Starting worker for agent: {agent}")
        # In a real implementation, this would start an Arq worker
        # filtered to the specific agent_type
        _start_worker(agent_filter=agent)
    else:
        click.echo("🦠 Starting Microbe orchestrator + all agent workers...")
        _start_all()


def _start_worker(agent_filter: str):
    """Start a single Arq worker filtered to a specific agent_type."""
    click.echo(
        f"  Worker listening for agent_type='{agent_filter}' "
        "on shared queue..."
    )

    # Check for worker.py in current directory
    if not Path("worker.py").exists():
        click.echo(
            "Error: No worker.py found. Are you in a Microbe project?",
            err=True,
        )
        sys.exit(1)

    os.environ["MICROBE_AGENT_FILTER"] = agent_filter
    subprocess.run(
        [sys.executable, "-m", "arq", "worker.WorkerSettings"],
        check=True,
    )


def _start_all():
    """Start the orchestrator and all discovered agent workers."""
    agents_dir = Path("agents")

    if not agents_dir.exists():
        click.echo(
            "Error: No 'agents/' directory found. "
            "Are you in a Microbe project?",
            err=True,
        )
        sys.exit(1)

    agents = list(agents_dir.glob("*.yaml")) + list(agents_dir.glob("*.yml"))

    if not agents:
        click.echo("No agents found in agents/ directory.", err=True)
        sys.exit(1)

    click.echo(f"  Found {len(agents)} agent(s):")
    for a in agents:
        click.echo(f"    • {a.stem}")

    click.echo("\n  Starting worker...")

    subprocess.run(
        [sys.executable, "-m", "arq", "worker.WorkerSettings"],
        check=True,
    )


if __name__ == "__main__":
    cli()
