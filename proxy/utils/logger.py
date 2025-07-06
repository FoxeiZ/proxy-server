import logging

from ..config import Config

__all__ = ("get_logger",)


def get_logger(
    name: str,
    level: str | int = Config.log_level,
    handler: type[logging.Handler] = logging.StreamHandler,
    formatter: str = "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
) -> logging.Logger:
    init_handler = handler()
    init_handler.setFormatter(logging.Formatter(formatter))

    logger = logging.getLogger(name)
    logger.setLevel("DEBUG" if Config.debug else level or "INFO")
    logger.addHandler(init_handler)
    return logger
