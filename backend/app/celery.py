"""
Celery configuration for SolenOS asynchronous processing.
"""
from celery import Celery
from app.core.config import get_settings

# Get settings
settings = get_settings()

# Create Celery instance
celery_app = Celery(
    "solenos",
    broker=settings.redis_url if hasattr(settings, 'redis_url') else "redis://localhost:6379/0",
    backend=settings.redis_url if hasattr(settings, 'redis_url') else "redis://localhost:6379/0",
    include=[
        "app.services.document_processing",
        "app.services.situation",
        "app.services.care_summary",
        # Add other service modules as needed for asynchronous processing
    ]
)

# Configure Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_max_tasks_per_child=1000,
)