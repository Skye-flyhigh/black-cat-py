"""Configuration loading utilities."""

import json
from pathlib import Path

from nanobot.config.schema import Config
from nanobot.utils.helpers import convert_keys, convert_to_camel, get_data_path


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.home() / ".nanobot" / "config.json"


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    return get_data_path()


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()

    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            data = _migrate_config(data)

            # Extract MCP servers before key conversion — env vars and HTTP
            # headers contain arbitrary keys that must not be mangled.
            raw_mcp = data.get("tools", {}).pop("mcpServers", None)

            converted = convert_keys(data)

            if raw_mcp is not None:
                converted.setdefault("tools", {})["mcp_servers"] = raw_mcp

            return Config.model_validate(converted)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to camelCase format
    data = config.model_dump()

    # Extract MCP servers before camelCase conversion — env vars and HTTP
    # headers contain arbitrary keys that must not be mangled.
    raw_mcp = data.get("tools", {}).pop("mcp_servers", None)

    data = convert_to_camel(data)

    if raw_mcp is not None:
        data.setdefault("tools", {})["mcpServers"] = raw_mcp

    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data
