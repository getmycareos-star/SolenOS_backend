"""
Observability Plugin for SolenOS
Provides logging, metrics, and tracing capabilities
"""

from fastapi import APIRouter
from observability.middleware import setup_observability_middleware

router = APIRouter(tags=["observability"])

@router.get("/health/observability")
async def observability_health():
    """Health check for observability components"""
    from observability.metrics import metrics
    from observability.logging import logger
    
    return {
        "status": "healthy",
        "components": {
            "logging": "enabled",
            "metrics": "enabled", 
            "tracing": "enabled"
        },
        "metrics_collected": len(metrics.get_all_metrics()["counters"]) + 
                           len(metrics.get_all_metrics()["gauges"]) +
                           len(metrics.get_all_metrics()["histograms"])
    }

# This function will be called by the plugin loader
def init_plugin(app):
    """Initialize the observability plugin"""
    setup_observability_middleware(app)
    # Include the observability router
    app.include_router(router, prefix="/observability")