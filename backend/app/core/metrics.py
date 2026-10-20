"""Prometheus metrics collection for SolenOS."""

from prometheus_client import Counter, Histogram, Gauge, Info
import time
import logging
from typing import Callable
from functools import wraps

logger = logging.getLogger(__name__)

# Application info
APP_INFO = Info(
    'solenos_app_info',
    'Application information',
    ['version', 'environment']
)

# HTTP request metrics
HTTP_REQUESTS_TOTAL = Counter(
    'solenos_http_requests_total',
    'Total HTTP requests',
    ['method', 'endpoint', 'http_status']
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    'solenos_http_request_duration_seconds',
    'HTTP request duration in seconds',
    ['method', 'endpoint']
)

# Database metrics
DATABASE_CONNECTIONS_ACTIVE = Gauge(
    'solenos_database_connections_active',
    'Number of active database connections'
)

DATABASE_QUERY_DURATION_SECONDS = Histogram(
    'solenos_database_query_duration_seconds',
    'Database query duration in seconds',
    ['operation', 'table']
)

# Cache metrics
CACHE_HITS_TOTAL = Counter(
    'solenos_cache_hits_total',
    'Total cache hits',
    ['cache_type']
)

CACHE_MISSES_TOTAL = Counter(
    'solenos_cache_misses_total',
    'Total cache misses',
    ['cache_type']
)

# Business metrics
CARE_EVENTS_TOTAL = Counter(
    'solenos_care_events_total',
    'Total care events processed',
    ['event_type', 'status']
)

EVIDENCE_ITEMS_TOTAL = Counter(
    'solenos_evidence_items_total',
    'Total evidence items processed',
    ['evidence_type', 'status']
)

ACTIVE_SESSIONS = Gauge(
    'solenos_active_sessions',
    'Number of active user sessions'
)

UPLOADS_TOTAL = Counter(
    'solenos_uploads_total',
    'Total file uploads',
    ['file_type', 'status']
)

# Error metrics
ERRORS_TOTAL = Counter(
    'solenos_errors_total',
    'Total errors',
    ['error_type', 'component']
)

# Initialize app info
def init_app_info(version: str, environment: str):
    """Initialize application info metric."""
    try:
        APP_INFO.info({
            'version': version,
            'environment': environment
        })
    except Exception as e:
        # Handle prometheus-client version compatibility issues
        logger.warning(f"Could not initialize app info metric: {e}")


def monitor_http_request(method: str, endpoint: str):
    """Decorator to monitor HTTP requests."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                # Extract status code from result if it's a Response
                http_status = getattr(result, 'status_code', 200)
                HTTP_REQUESTS_TOTAL.labels(
                    method=method,
                    endpoint=endpoint,
                    http_status=str(http_status)
                ).inc()
                return result
            except Exception as e:
                HTTP_REQUESTS_TOTAL.labels(
                    method=method,
                    endpoint=endpoint,
                    http_status="500"
                ).inc()
                raise
            finally:
                duration = time.time() - start_time
                HTTP_REQUEST_DURATION_SECONDS.labels(
                    method=method,
                    endpoint=endpoint
                ).observe(duration)
        return async_wrapper
    return decorator


def monitor_database_query(operation: str, table: str):
    """Decorator to monitor database queries."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                DATABASE_QUERY_DURATION_SECONDS.labels(
                    operation=operation,
                    table=table
                ).observe(time.time() - start_time)
                return result
            except Exception as e:
                ERRORS_TOTAL.labels(
                    error_type=type(e).__name__,
                    component="database"
                ).inc()
                raise
        return sync_wrapper
    return decorator


def increment_cache_hits(cache_type: str):
    """Increment cache hit counter."""
    CACHE_HITS_TOTAL.labels(cache_type=cache_type).inc()


def increment_cache_misses(cache_type: str):
    """Increment cache miss counter."""
    CACHE_MISSES_TOTAL.labels(cache_type=cache_type).inc()


def increment_care_events(event_type: str, status: str):
    """Increment care events counter."""
    CARE_EVENTS_TOTAL.labels(event_type=event_type, status=status).inc()


def increment_evidence_items(evidence_type: str, status: str):
    """Increment evidence items counter."""
    EVIDENCE_ITEMS_TOTAL.labels(evidence_type=evidence_type, status=status).inc()


def set_active_sessions(count: int):
    """Set active sessions gauge."""
    ACTIVE_SESSIONS.set(count)


def increment_uploads(file_type: str, status: str):
    """Increment uploads counter."""
    UPLOADS_TOTAL.labels(file_type=file_type, status=status).inc()


def increment_errors(error_type: str, component: str):
    """Increment errors counter."""
    ERRORS_TOTAL.labels(error_type=error_type, component=component).inc()