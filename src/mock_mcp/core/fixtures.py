"""
Fixture Loader for Shared Fixture Data across Scenarios.
Loads reusable YAML fixture files from config/fixtures/ to seed session state.
"""

from pathlib import Path
from typing import Any, Dict, List, Union
import yaml

from mock_mcp.logger import logger


class FixtureLoader:
    """Helper class to load and merge YAML fixtures."""

    def __init__(self, fixtures_dir: Union[str, Path] = "config/fixtures"):
        self.fixtures_dir = Path(fixtures_dir)

    def load_fixture_file(self, filename: str) -> Dict[str, Any]:
        """Load a single fixture YAML file."""
        file_path = self.fixtures_dir / filename
        if not file_path.exists():
            logger.warning(f"Fixture file not found: {file_path}")
            return {}

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                logger.info(f"Loaded fixture file: {file_path}")
                return data
        except Exception as exc:
            logger.error(f"Failed to parse fixture file {file_path}: {exc}")
            return {}

    def load_fixtures(self, fixture_filenames: List[str]) -> Dict[str, Any]:
        """Load and deep-merge multiple fixture files."""
        combined_state: Dict[str, Any] = {}
        for fname in fixture_filenames:
            data = self.load_fixture_file(fname)
            for key, value in data.items():
                if isinstance(value, dict) and key in combined_state and isinstance(combined_state[key], dict):
                    # Merge nested dictionaries (e.g. user databases or system settings)
                    combined_state[key].update(value)
                else:
                    combined_state[key] = value
        return combined_state


# Global singleton instance
fixture_loader = FixtureLoader()
