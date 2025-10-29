import argparse
import os
from typing import Any, Dict

from dotenv import find_dotenv, load_dotenv

from .singleton import Singleton


class ConfigSingleton(Singleton):
    """A singleton class for managing configuration values from environment variables
    and command-line arguments.
    """

    def __init__(self) -> None:
        """Initialize the configuration singleton."""
        self._config: Dict[str, Any] = {}
        self._parse_args()
        self._load_from_env()

    def _parse_args(self) -> None:
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
        parser.add_argument(
            "--addr",
            dest="ADDR",
            default="0.0.0.0:5000",
            help="address to bind the server to, default is '0.0.0.0:5000'",
        )
        parser.add_argument(
            "--cache-max-items",
            dest="CACHE_MAX_ITEMS",
            type=int,
            default=500,
            help="maximum number of items in resource cache",
        )
        parser.add_argument(
            "--cache-max-memory-mb",
            dest="CACHE_MAX_MEMORY_MB",
            type=int,
            default=100,
            help="maximum memory usage in MB for resource cache",
        )
        parser.add_argument(
            "--cache-ttl-seconds",
            dest="CACHE_TTL_SECONDS",
            type=int,
            default=3600,
            help="cache time-to-live in seconds",
        )
        parser.add_argument(
            "--cache-max-item-size-mb",
            dest="CACHE_MAX_ITEM_SIZE_MB",
            type=int,
            default=10,
            help="maximum size in MB for a single cached item",
        )

        args, _ = parser.parse_known_args()
        for key, value in vars(args).items():
            if value is not None:
                self._config[key] = value

    def _load_from_env(self) -> None:
        """Load configuration from environment variables (if not already set by args)."""
        env_vars: Dict[str, Any] = {
            "LOG_LEVEL": "INFO",
            "LOG_FUNCTION_CALL": False,
            "GALLERY_PATH": "galleries",
            "ADDR": "0.0.0.0:5000",
            "CACHE_MAX_ITEMS": 500,
            "CACHE_MAX_MEMORY_MB": 100,
            "CACHE_TTL_SECONDS": 3600,
            "CACHE_MAX_ITEM_SIZE_MB": 10,
        }

        load_dotenv()
        load_dotenv(find_dotenv(".env.local"))

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
    def cache_path(self) -> str:
        """Get the cache path."""
        return os.path.join(self.gallery_path, ".cache")

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

    @property
    def addr(self) -> str:
        """Get the address to bind the server to."""
        return self._config.get("ADDR", "0.0.0.0:5000")

    @property
    def host(self) -> str:
        """Get the host to bind the server to."""
        return self.addr.split(":")[0]

    @property
    def port(self) -> int:
        """Get the port to bind the server to."""
        return int(self.addr.split(":")[1]) if ":" in self.addr else 5000

    @property
    def cache_max_items(self) -> int:
        """Get the maximum number of items in resource cache."""
        return self._config.get("CACHE_MAX_ITEMS", 500)

    @property
    def cache_max_memory_mb(self) -> int:
        """Get the maximum memory usage in MB for resource cache."""
        return self._config.get("CACHE_MAX_MEMORY_MB", 100)

    @property
    def cache_ttl_seconds(self) -> int:
        """Get the cache time-to-live in seconds."""
        return self._config.get("CACHE_TTL_SECONDS", 3600)

    @property
    def cache_max_item_size_mb(self) -> int:
        """Get the maximum size in MB for a single cached item."""
        return self._config.get("CACHE_MAX_ITEM_SIZE_MB", 10)


Config = ConfigSingleton()
