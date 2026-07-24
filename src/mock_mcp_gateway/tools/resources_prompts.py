"""
Resource and Prompt implementations.
"""

from datetime import datetime, timezone
import json
import time

from mock_mcp_gateway.core.session import session_manager

_SERVER_START_TIME = time.time()


def system_status_resource() -> str:
    """Return real-time JSON status of the Mock MCP server."""
    session = session_manager.get_session()
    status = {
        "status": "healthy",
        "session_id": session.session_id,
        "scenario_id": session.scenario_id,
        "server_id": session.server_id,
        "uptime_seconds": round(time.time() - _SERVER_START_TIME, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "registered_users": len(session.user_db),
        "metrics": session.state.get("system_info"),
    }
    return json.dumps(status, indent=2)


def users_list_resource() -> str:
    """Return the current list of session-isolated users as JSON formatted string."""
    session = session_manager.get_session()
    return json.dumps(list(session.user_db.values()), indent=2)


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
