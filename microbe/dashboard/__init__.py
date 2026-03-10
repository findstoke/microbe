"""
Microbe Dashboard — Web UI for monitoring workflows.

Provides a FastAPI application with HTMX-powered real-time
DAG visualization and task management.
"""

from microbe.dashboard.app import create_app

__all__ = ["create_app"]
