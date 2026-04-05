from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

from ticktick_mcp.client import TickTickClient
from ticktick_mcp.models import Column, Project
from ticktick_mcp.resolve import resolve_name, resolve_name_with_etag


def _get_client(ctx: Context) -> TickTickClient:
    return ctx.request_context.lifespan_context["client"]  # type: ignore[union-attr]


async def _resolve_project_id(client: TickTickClient, project: str) -> str:
    """Resolve a project name/ID."""
    projects = await client.v1_get("/project")
    parsed = [Project(**p) for p in projects]
    return resolve_name(project, parsed, lambda p: p.name, lambda p: p.id, "project")


async def _resolve_section_id(
    client: TickTickClient, project_id: str, name_or_id: str
) -> tuple[str, str]:
    """Resolve a section name/ID within a project to (id, etag)."""
    columns = await client.v2_get(f"/column/project/{project_id}")
    parsed = [Column(**c) for c in columns]
    if not parsed:
        raise ToolError(f"Project {project_id} has no sections")
    return resolve_name_with_etag(
        name_or_id,
        parsed,
        lambda c: c.name,
        lambda c: c.id,
        lambda c: c.etag or "",
        "section",
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    async def list_sections(
        ctx: Context,
        project: str,
    ) -> list[dict[str, Any]]:
        """List all sections (columns) in a project.

        Sections organize tasks within a project into groups (e.g., store
        sections in Groceries, cuisine types in Recipes). Requires v2 session
        token.

        Args:
            project: Project name or ID. Supports fuzzy matching.
        """
        client = _get_client(ctx)
        pid = await _resolve_project_id(client, project)
        return await client.v2_get(f"/column/project/{pid}")

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def create_section(
        ctx: Context,
        project: str,
        name: str,
        sort_order: int | None = None,
    ) -> Any:
        """Create a new section (column) in a project.

        Args:
            project: Project name or ID. Supports fuzzy matching.
            name: Section name (required).
            sort_order: Optional sort position. Lower values appear first.
        """
        client = _get_client(ctx)
        pid = await _resolve_project_id(client, project)

        import time
        import uuid

        column_id = str(uuid.uuid4()).replace("-", "")[:24]
        now = time.strftime("%Y-%m-%dT%H:%M:%S.000+0000")

        payload: dict[str, Any] = {
            "add": [
                {
                    "id": column_id,
                    "projectId": pid,
                    "name": name,
                    "sortOrder": sort_order if sort_order is not None else 0,
                    "createdTime": now,
                }
            ]
        }
        result = await client.v2_post("/batch/column", payload)
        return {"columnId": column_id, "result": result}

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def update_section(
        ctx: Context,
        project: str,
        section: str,
        name: str | None = None,
        sort_order: int | None = None,
    ) -> Any:
        """Update a section's name or sort order.

        Args:
            project: Project name or ID. Supports fuzzy matching.
            section: Section name or ID to update. Supports fuzzy matching.
            name: New section name.
            sort_order: New sort position.
        """
        client = _get_client(ctx)
        pid = await _resolve_project_id(client, project)
        sid, etag = await _resolve_section_id(client, pid, section)

        update_data: dict[str, Any] = {
            "id": sid,
            "projectId": pid,
            "etag": etag,
        }
        if name is not None:
            update_data["name"] = name
        if sort_order is not None:
            update_data["sortOrder"] = sort_order

        return await client.v2_post("/batch/column", {"update": [update_data]})

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def delete_section(
        ctx: Context,
        project: str,
        section: str,
    ) -> str:
        """Delete a section from a project.

        Tasks in the deleted section are moved to the default (unsectioned) area.

        Args:
            project: Project name or ID. Supports fuzzy matching.
            section: Section name or ID to delete. Supports fuzzy matching.
        """
        client = _get_client(ctx)
        pid = await _resolve_project_id(client, project)
        sid, _ = await _resolve_section_id(client, pid, section)

        await client.v2_post(
            "/batch/column",
            {"delete": [{"projectId": pid, "columnId": sid}]},
        )
        return f"Section {sid} deleted from project {pid}"

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def move_task_to_section(
        ctx: Context,
        task_id: str,
        project: str,
        section: str | None = None,
    ) -> str:
        """Move a task into a specific section (column) within a project.

        Can also move a task out of a section by omitting the section parameter.

        Args:
            task_id: The task ID to move.
            project: Project name or ID. Supports fuzzy matching.
            section: Section name or ID. Omit to move to the unsectioned area.
        """
        client = _get_client(ctx)
        pid = await _resolve_project_id(client, project)

        column_id = ""
        if section:
            column_id, _ = await _resolve_section_id(client, pid, section)

        # Use v2 batch/task to update the columnId on the task
        # First get the task to preserve its etag
        task = await client.v1_get(f"/project/{pid}/task/{task_id}")
        etag = task.get("etag", "")

        await client.v2_post(
            "/batch/task",
            {
                "update": [
                    {
                        "id": task_id,
                        "projectId": pid,
                        "columnId": column_id,
                        "etag": etag,
                    }
                ]
            },
        )
        dest = section or "unsectioned"
        return f"Task {task_id} moved to section '{dest}' in project {pid}"
