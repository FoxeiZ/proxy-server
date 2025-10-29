import logging
from typing import Type

from ..config import Config

__all__ = ("get_logger",)


def get_logger(
    name: str,
    level: str | int = Config.log_level,
    handler: Type[logging.Handler] = logging.StreamHandler,
    formatter: str = "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    init_handler = handler()
    init_handler.setFormatter(logging.Formatter(formatter))

    logger.setLevel("DEBUG" if Config.debug else level or "INFO")
    logger.addHandler(init_handler)
    logger.propagate = False
    return logger
