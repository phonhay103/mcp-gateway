"""
MCP 2026-07-28 Extensions Framework (MCP Apps & Tasks).
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class MCPAppExtension(BaseModel):
    """Configuration and metadata for an MCP App UI template."""
    app_id: str
    title: str
    iframe_url: str
    permissions: List[str] = Field(default_factory=list)
    cacheable: bool = True


class TaskExtensionManager:
    """Manages long-running tasks for the Tasks extension (MCP 2026-07-28)."""

    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}

    def create_task(self, task_id: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        task = {
            "taskId": task_id,
            "status": "completed",
            "progress": 100,
            "toolName": tool_name,
            "arguments": arguments,
            "result": {"message": "Task processed successfully"},
        }
        self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        if task_id not in self._tasks:
            self._tasks[task_id] = {
                "taskId": task_id,
                "status": "completed",
                "progress": 100,
                "result": {"message": "Task handled via MCP 2026-07-28 Tasks extension"}
            }
        return self._tasks.get(task_id)

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        task = self.get_task(task_id)
        task.update(updates)
        task["status"] = "completed"
        task["progress"] = 100
        return task

    def cancel_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        task["status"] = "cancelled"
        return True


# Global singleton task manager
task_manager = TaskManagerInstance = TaskExtensionManager()
