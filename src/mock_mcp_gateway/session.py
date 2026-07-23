"""
Session & Scenario State Management with 3-Way Context Isolation:
(session_id, scenario_id, server_id)

Guarantees concurrent execution across multiple Scenarios, multiple MCP Servers, and multiple Sessions without state leakage.
Provides session lifecycle endpoints (Create, Close/Destroy, Query).
Uses UUID v7 (Time-Sortable) for auto-generated session_ids.
"""

from contextvars import ContextVar
import copy
from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from mock_mcp_gateway.logger import logger


def generate_uuid7() -> str:
    """Generate a time-sortable UUID v7 string natively supported in Python 3.14+."""
    if hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())
    return str(uuid.uuid4())


# ContextVars storing active execution context for python request scope
current_session_id: ContextVar[str] = ContextVar("current_session_id", default="default_session")
current_scenario_id: ContextVar[str] = ContextVar("current_scenario_id", default="default_scenario")
current_server_id: ContextVar[str] = ContextVar("current_server_id", default="default_server")


class SessionData(BaseModel):
    """Isolated state container for a specific (session_id, scenario_id, server_id) tuple."""

    session_id: str
    scenario_id: str = "default_scenario"
    server_id: str = "default_server"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_active: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    state: Dict[str, Any] = Field(default_factory=dict)

    def touch(self) -> None:
        self.last_active = datetime.now(timezone.utc).isoformat()

    @property
    def user_db(self) -> Dict[str, Any]:
        """Convenience accessor for user database stored in state."""
        if "users" not in self.state:
            self.state["users"] = {}
        return self.state["users"]

    def get_error_simulation(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Return MCP error simulation rule for tool if defined in scenario state."""
        sims = self.state.get("_error_simulations", [])
        for rule in sims:
            if isinstance(rule, dict) and rule.get("tool") == tool_name:
                return rule
        return None


class SessionManager:
    """Thread-safe state manager with composite key isolation and lifecycle methods."""

    def __init__(self):
        self._sessions: Dict[str, SessionData] = {}

    def _make_key(self, session_id: str, scenario_id: str, server_id: str) -> str:
        return f"{session_id}::{scenario_id}::{server_id}"

    def get_or_create_session(
        self,
        session_id: Optional[str] = None,
        scenario_id: Optional[str] = None,
        server_id: Optional[str] = None,
        initial_state: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionData:
        """Get or initialize isolated session state seeded with scenario fixtures."""
        sid = session_id or current_session_id.get()
        sc_id = scenario_id or current_scenario_id.get()
        srv_id = server_id or current_server_id.get()

        key = self._make_key(sid, sc_id, srv_id)

        if key not in self._sessions:
            seed_state = copy.deepcopy(initial_state) if initial_state else {}
            if "users" not in seed_state:
                seed_state["users"] = {
                    "user_1": {"id": "user_1", "name": "Alice Johnson", "email": "alice@example.com", "role": "admin"},
                    "user_2": {"id": "user_2", "name": "Bob Smith", "email": "bob@example.com", "role": "user"},
                }

            self._sessions[key] = SessionData(
                session_id=sid,
                scenario_id=sc_id,
                server_id=srv_id,
                metadata=metadata or {},
                state=seed_state,
            )
            logger.info(f"Initialized isolated state key [{key}]")
        else:
            self._sessions[key].touch()
            if metadata:
                self._sessions[key].metadata.update(metadata)

        return self._sessions[key]

    def get_session(
        self,
        session_id: Optional[str] = None,
        scenario_id: Optional[str] = None,
        server_id: Optional[str] = None,
    ) -> SessionData:
        """Get session for given parameters or active contextvars."""
        return self.get_or_create_session(
            session_id=session_id, scenario_id=scenario_id, server_id=server_id
        )

    def close_session(self, session_id: str) -> int:
        """Completely close and evict all state records for a given session ID."""
        keys_to_remove = [k for k in self._sessions.keys() if k.startswith(f"{session_id}::")]
        for k in keys_to_remove:
            self._sessions.pop(k, None)

        logger.info(f"Closed session '{session_id}', evicted {len(keys_to_remove)} state keys")
        return len(keys_to_remove)

    def clear_session(self, session_id: str, scenario_id: Optional[str] = None) -> int:
        """Clear cached state for a session ID (optionally scoped to a scenario ID)."""
        keys_to_remove = []
        for key in list(self._sessions.keys()):
            if scenario_id:
                if key.startswith(f"{session_id}::{scenario_id}::"):
                    keys_to_remove.append(key)
            else:
                if key.startswith(f"{session_id}::"):
                    keys_to_remove.append(key)

        for k in keys_to_remove:
            self._sessions.pop(k, None)

        logger.info(f"Cleared {len(keys_to_remove)} session records for session_id='{session_id}'")
        return len(keys_to_remove)

    def list_active_sessions(self) -> List[Dict[str, Any]]:
        """Return a list of unique active session IDs and their metadata."""
        unique_sessions: Dict[str, Dict[str, Any]] = {}
        for key, sdata in self._sessions.items():
            sid = sdata.session_id
            if sid not in unique_sessions:
                unique_sessions[sid] = {
                    "session_id": sid,
                    "created_at": sdata.created_at,
                    "last_active": sdata.last_active,
                    "active_scenarios": set(),
                    "metadata": sdata.metadata,
                }
            unique_sessions[sid]["active_scenarios"].add(sdata.scenario_id)
            if sdata.last_active > unique_sessions[sid]["last_active"]:
                unique_sessions[sid]["last_active"] = sdata.last_active

        result = []
        for sid, info in unique_sessions.items():
            info["active_scenarios"] = list(info["active_scenarios"])
            result.append(info)
        return result


# Global singleton instance
session_manager = SessionManager()


class SessionMiddleware(BaseHTTPMiddleware):
    """
    Middleware establishing session, scenario, and server isolation context per HTTP request.
    Extracts headers:
      - X-Session-ID (or custom header)
      - X-MCP-Scenario (optional header override)
    Generates time-sortable UUID v7 if X-Session-ID header is missing.
    """

    def __init__(
        self,
        app,
        header_session_name: str = "X-Session-ID",
        header_scenario_name: str = "X-MCP-Scenario",
        default_scenario_id: str = "default_scenario",
        default_server_id: str = "default_server",
        initial_fixture_state: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(app)
        self.header_session_name = header_session_name
        self.header_scenario_name = header_scenario_name
        self.default_scenario_id = default_scenario_id
        self.default_server_id = default_server_id
        self.initial_fixture_state = initial_fixture_state or {}

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. Extract or generate Session ID using UUID v7
        session_id = request.headers.get(self.header_session_name) or generate_uuid7()

        # 2. Extract Scenario ID (Header override > middleware default)
        scenario_id = request.headers.get(self.header_scenario_name) or self.default_scenario_id

        # 3. Extract Server ID
        server_id = self.default_server_id

        # Set ContextVars
        token_sess = current_session_id.set(session_id)
        token_scen = current_scenario_id.set(scenario_id)
        token_srv = current_server_id.set(server_id)

        request.state.session_id = session_id
        request.state.scenario_id = scenario_id
        request.state.server_id = server_id

        # Ensure session instance exists in SessionManager
        session_manager.get_or_create_session(
            session_id=session_id,
            scenario_id=scenario_id,
            server_id=server_id,
            initial_state=self.initial_fixture_state,
        )

        try:
            response: Response = await call_next(request)
            response.headers[self.header_session_name] = session_id
            response.headers[self.header_scenario_name] = scenario_id
            return response
        finally:
            current_session_id.reset(token_sess)
            current_scenario_id.reset(token_scen)
            current_server_id.reset(token_srv)
