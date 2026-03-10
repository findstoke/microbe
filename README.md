### Note: 

I'm still actively working on adding in additional error handling and publishing to PyPI. If you want to try it, you can install via:

```bash
pip install git+https://github.com/findstoke/microbe.git
```


# 🦠 Microbe

**Microservices principles applied to AI agents.**

Each agent is an independently deployable worker. Workflows are human-readable YAML DAGs that non-technical users can edit. Agents communicate through a single shared Redis queue.

```
Workflow YAML → Orchestrator → Redis Queue → Agent Workers → Results
```

## Why Microbe?

Most agent frameworks (LangChain, CrewAI) define all steps upfront in code. This fails for open-ended tasks where the work itself surfaces new unknowns.

Microbe is different:

- **Runtime DAG expansion** -> agents can spawn new steps mid-execution. The graph reflects reality, not assumptions.
- **Microbe Dashboard** -> real-time DAG visualization and monitoring. See your agents move blocks and spawn work.
- **Independently deployable agents** -> each agent is its own worker process. Scale searchers separately from planners.
- **Zero-infrastructure mode** -> run locally with zero config (SQLite + In-memory queue). No Docker or Redis required.
- **YAML-first** -> workflows and agents are YAML files. Edit them without touching code. Non-technical collaborators can adjust workflows directly.
- **Single shared queue** -> one Redis queue, workers filter by `agent_type`. Operational complexity doesn't scale with agent count.

## Quick Start

```bash
pip install "microbe[dashboard]"

# Scaffold a new project
microbe init my-project
cd my-project

# Add your API keys to .env, then:
microbe run

# In a new terminal, open the visual dashboard:
microbe dashboard
```

## How It Works

### 1. Define Agents (YAML)

```yaml
# agents/planner.yaml
name: planner
agent_type: planner
description: "Breaks queries into search strategies"
model: llama-3.3-70b-versatile
provider: groq
temperature: 0.2
response_format: json
system_prompt: |
  Break the query into 3-5 search strategies.
  Return JSON: { "queries": ["...", "..."] }
```

### 2. Define Workflows (YAML)

```yaml
# workflows/research.yaml
name: research
description: "Research a topic"

steps:
  - id: plan
    agent: planner
    input:
      query: "{{ trigger.query }}"

  - id: search
    agent: searcher
    depends_on: [plan]
    foreach: "{{ steps.plan.output.queries }}"
    input:
      query: "{{ item }}"

  - id: synthesize
    agent: synthesizer
    depends_on: [search]
    input:
      results: "{{ steps.search.output.* }}"
```

### 3. Run & Visualize

```bash
# Start the local embedded runner (SQLite + Memory Queue)
microbe run

# Open the dashboard (FastAPI + HTMX + WebSocket)
microbe dashboard --port 8420
```

## Dashboard

Microbe includes a real-time monitoring dashboard inspired by **Uptime Kuma**. It features:

- **Live DAG Visualization**: Watch steps move from pending to running to completed.
- **Heartbeat Bars**: Visual health check of all your active tasks and step progress.
- **Fan-out Badges**: Deep visibility into parallelized `foreach` steps and dynamic spawning.
- **Historical Analysis**: Review past execution results, token usage, and error logs.

## Key Concepts

| Concept          | Description                                                     |
| ---------------- | --------------------------------------------------------------- |
| **Agent**        | A single-purpose worker defined by YAML + optional Python class |
| **Workflow**     | A YAML DAG defining steps, ordering, and data flow              |
| **Orchestrator** | Dispatches steps, collects results, advances the DAG            |
| **Step**         | One unit of work = one agent invocation                         |

## Workflow Features

- **`depends_on`** — DAG ordering. Independent steps run in parallel.
- **`foreach`** — Fan-out. Run a step once per item in a list.
- **`{{ }}`** — Template expressions for trigger data, step outputs, environment vars.
- **Runtime spawning** — Agents can return `spawn` in their output to create new steps dynamically.

## Custom Agents (Python)

For logic beyond LLM calls, create a Python agent:

```python
from microbe import Agent, StepResult

class MyAgent(Agent):
    async def execute(self, input_data: dict, context: dict) -> StepResult:
        # Your custom logic here
        result = await some_api_call(input_data["query"])
        return StepResult(
            data={"result": result},
            spawn=[  # Runtime DAG expansion
                {
                    "agent": "analyzer",
                    "input": {"url": url}
                }
                for url in result["urls"]
            ]
        )
```

## Architecture

```
┌─────────────────────┐
│     Orchestrator     │
│  Reads workflow YAML │
│  Dispatches steps    │
│  Advances DAG        │
└────────┬─────────────┘
         │
    Redis Queue (single)
    Tasks tagged with agent_type
         │
    ┌────┴────┬──────────┐
    ▼         ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐
│Planner │ │Searcher│ │  Your  │
│Worker  │ │Worker  │ │ Agent  │
└────────┘ └────────┘ └────────┘
```

## CLI

```bash
microbe init <name>         # Scaffold a project
microbe new-agent <name>    # Add an agent
microbe run                 # Start local embedded runner
microbe run --redis-url ... # Start production workers
microbe dashboard           # Launch the web dashboard
```

## License

MIT
