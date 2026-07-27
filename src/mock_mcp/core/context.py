from contextvars import ContextVar
from typing import Any, Dict

# Active scenario ID (e.g., 'full-scenario', 'sysops-scenario')
current_scenario_id: ContextVar[str] = ContextVar("current_scenario_id", default="default_scenario")

# Dictionary mapping scenario_id -> fixture state (e.g. loaded user lists, system metrics)
scenario_states: Dict[str, Dict[str, Any]] = {}

# Dictionary mapping scenario_id -> ScenarioConfig
scenario_configs_map: Dict[str, Any] = {}
