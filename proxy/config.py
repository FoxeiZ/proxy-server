import argparse
import os
from typing import Any

from .singleton import Singleton


class ConfigSingleton(Singleton):
    """
    A singleton class for managing configuration values from environment variables
    and command-line arguments.
    """

    def __init__(self):
        """Initialize the configuration singleton."""
        self._config = {}
        self._parse_args()
        self._load_from_env()

    def _parse_args(self):
        """Parse command line arguments."""
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--debug",
            dest="LOG_LEVEL",
            action="store_const",
            const="DEBUG",
            help="Enable debug mode, short for --log-level=DEBUG",
        )
        parser.add_argument(
            "--log-function-call",
            dest="LOG_FUNCTION_CALL",
            action="store_true",
            help="Log function calls. Use with --debug or --log-level=DEBUG",
        )
        parser.add_argument(
            "--gallery-path",
            dest="GALLERY_PATH",
            default="galleries",
            help="Path to the gallery directory",
        )

        args, _ = parser.parse_known_args()
        for key, value in vars(args).items():
            if value is not None:
                self._config[key] = value

    def _load_from_env(self):
        """Load configuration from environment variables (if not already set by args)."""
        env_vars = {
            "LOG_LEVEL": "INFO",
            "LOG_FUNCTION_CALL": False,
            "GALLERY_PATH": "galleries",
        }

        for key, default in env_vars.items():
            if key not in self._config:
                env_value = os.environ.get(key)
                if env_value is not None:
                    if isinstance(default, bool):
                        self._config[key] = env_value.lower() in ("1", "true", "yes")
                    else:
                        self._config[key] = env_value
                elif default is not None:
                    self._config[key] = default

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key."""
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value."""
        self._config[key] = value

    @property
    def gallery_path(self) -> str:
        """Get the gallery path."""
        return self._config.get("GALLERY_PATH", "galleries")

    @property
    def debug(self) -> bool:
        """Get the debug mode."""
        return self.log_level == "DEBUG"

    @property
    def log_level(self) -> str:
        """Get the logging level."""
        return self._config.get("LOG_LEVEL", "INFO")

    @property
    def log_function_call(self) -> bool:
        """Get the log function call setting."""
        return self._config.get("LOG_FUNCTION_CALL", False)


Config = ConfigSingleton()
