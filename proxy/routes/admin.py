from __future__ import annotations

from typing import TYPE_CHECKING

from quart import Blueprint, jsonify

from ..utils import ResourceCache
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from quart import Quart

__all__ = ("register_routes",)

logger = get_logger(__name__)
bp = Blueprint("admin", __name__)


@bp.route("/cache/stats", methods=["GET"])
async def cache_stats():
    """Get cache statistics with domain-level analytics."""
    cache = ResourceCache()
    stats = cache.get_stats()

    return jsonify(
        {
            "hits": stats.hits,
            "misses": stats.misses,
            "domain_hits": stats.domain_hits,
            "url_hits": stats.url_hits,
            "size": stats.size,
            "memory_usage_mb": round(stats.memory_usage / 1024 / 1024, 2),
            "hit_rate_percent": round(stats.hit_rate, 2),
            "domain_hit_rate_percent": round(stats.domain_hit_rate, 2),
        }
    )


@bp.route("/cache/clear", methods=["POST"])
async def cache_clear():
    """Clear all cached entries."""
    cache = ResourceCache()
    cache.clear()
    logger.info("cache cleared via admin endpoint")
    return jsonify({"message": "cache cleared successfully"})


@bp.route("/cache/cleanup", methods=["POST"])
async def cache_cleanup():
    """Manually trigger cache cleanup."""
    cache = ResourceCache()
    expired_count = cache.cleanup_expired()
    stats = cache.get_stats()

    logger.info(
        f"manual cache cleanup completed: removed {expired_count} expired entries"
    )

    return jsonify(
        {
            "message": f"cleanup completed, removed {expired_count} expired entries",
            "current_stats": {
                "size": stats.size,
                "memory_usage_mb": round(stats.memory_usage / 1024 / 1024, 2),
                "hit_rate_percent": round(stats.hit_rate, 2),
            },
        }
    )


def register_routes(app: Quart) -> None:
    """Register the admin routes with the given Quart app."""
    app.register_blueprint(bp, url_prefix="/admin")
