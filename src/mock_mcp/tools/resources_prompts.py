"""
Resource and Prompt implementations.
"""

from datetime import datetime, timezone
import json
import time

from mock_mcp.core.context import current_scenario_id, scenario_states

_SERVER_START_TIME = time.time()


def system_status_resource() -> str:
    """Return real-time JSON status of the Mock MCP server."""
    sc_id = current_scenario_id.get()
    state = scenario_states.get(sc_id, {})
    user_db = state.get("users", {})
    metrics = state.get("system_info")

    status = {
        "status": "healthy",
        "scenario_id": sc_id,
        "uptime_seconds": round(time.time() - _SERVER_START_TIME, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "registered_users": len(user_db),
        "metrics": metrics,
    }
    return json.dumps(status, indent=2)


def users_list_resource() -> str:
    """Return the current list of scenario-isolated users as JSON formatted string."""
    sc_id = current_scenario_id.get()
    state = scenario_states.get(sc_id, {})
    user_db = state.get("users", {})
    return json.dumps(list(user_db.values()), indent=2)


def code_review_prompt(language: str, code: str, focus: str = "performance and security") -> str:
    """Generate a prompt for reviewing code."""
    return f"""Please perform a thorough code review for the following {language} snippet.

Focus Areas: {focus}

Code:
```{language}
{code}
```

Provide constructive feedback and improvement suggestions.
"""
