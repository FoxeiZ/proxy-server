from __future__ import annotations

from typing import TYPE_CHECKING

from .func import register_routes as register_func_routes
from .galleries import register_routes as register_galleries_routes
from .next import register_routes as register_next_routes
from .proxy import register_routes as register_proxy_routes
from .root import register_routes as register_root_routes

if TYPE_CHECKING:
    from quart import Quart


def register_all_routes(app: "Quart") -> None:
    """Register all routes with the given Quart app."""
    register_proxy_routes(app)
    register_func_routes(app)
    register_galleries_routes(app)
    register_next_routes(app)
    register_root_routes(app)  # always last to handle redirects
