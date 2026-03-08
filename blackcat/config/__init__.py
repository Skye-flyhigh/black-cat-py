"""Configuration module for blackcat."""

from blackcat.config.loader import get_config_path, load_config
from blackcat.config.schema import Config

__all__ = ["Config", "load_config", "get_config_path"]
