import os
import time
import uuid
import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.routing import APIRoute
from app.core.config import get_settings
from app.api.v1 import router as v1_router
from app.api.notification import router as notification_router
from app.api.situation import router as situation_router
from app.api.caregiver_intelligence import router as caregiver_intelligence_router
from app.api.change import router as change_router
from app.api.care_summary import router as care_summary_router
from app.api.care_graph import router as care_graph_router
from app.api.companion import router as companion_router
from app.api.user import router as user_router
from app.api.weekly_review import router as weekly_review_router
from app.api.what_matters import router as what_matters_router
from app.api.what_should_i_do import router as what_should_i_do_router
from app.api.dementia import router as dementia_router
from app.api.files import router as files_router
from app.api.wellbeing import router as wellbeing_router
from app.api.daily_capture import router as daily_capture_router
from app.services.backup import backup_service
from app.api.daily_care import router as daily_care_router
from app.api.activation import router as activation_router
from app.api.job import router as job_router  # Job management endpoints
from app.api.reasoning import router as reasoning_router
from app.api.uncertainty import router as uncertainty_router
from app.api.knowledge_revision import router as knowledge_revision_router
from app.api.safety import router as safety_router
from app.api.uncertainty import router as uncertainty_router
from app.api.knowledge_revision import router as knowledge_revision_router
from app.api.safety import router as safety_router
from app.core.cache import cache_service  # Redis cache service
from app.core.security import add_security_headers
from app.services.alerting import send_security_alert
from app.core.metrics import init_app_info
from app.api.auth import ErrorResponse, router as auth_router
from app.core.database import engine
from sqlalchemy import text

settings = get_settings()

# Configure logging using our production logging module
try:
    from app.core.logging import logger
    # Logging is already configured in the logging module
except ImportError:
    # Fallback to basic logging if our module is not available
    log_level = logging.DEBUG if settings.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger("solenos")

# Rate limiting storage
# Simple in-memory rate limiter using fixed window counter
class RateLimiter:
    def __init__(self, requests_per_window: int, window_seconds: int):
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self.requests = defaultdict(list)  # key -> list of timestamps
    
    def is_allowed(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds
        
        # Clean old entries
        if key in self.requests:
            self.requests[key] = [ts for ts in self.requests[key] if ts > window_start]
        else:
            self.requests[key] = []
        
        # Check if under limit
        if len(self.requests[key]) < self.requests_per_window:
            self.requests[key].append(now)
            return True
        return False

# Rate limiter instances
# Different limits for different types of endpoints
auth_limiter = RateLimiter(requests_per_window=10, window_seconds=60)  # 10 per minute for auth
api_limiter = RateLimiter(requests_per_window=100, window_seconds=60)  # 100 per minute for general API
strict_limiter = RateLimiter(requests_per_window=5, window_seconds=60)  # 5 per minute for sensitive ops

def get_client_identifier(request: Request) -> str:
    """Get client identifier for rate limiting (IP address)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else "unknown"
    return ip

# CORS Configuration
def get_cors_origins():
    origins_env = os.getenv("ALLOWED_ORIGINS", "")
    origins = [origin.strip() for origin in origins_env.split(",") if origin.strip()]
    if settings.debug:
        # Common development origins
        dev_origins = [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
        for origin in dev_origins:
            if origin not in origins:
                origins.append(origin)
    return origins

# Trusted Host Configuration
def get_trusted_hosts():
    hosts_env = os.getenv("ALLOWED_HOSTS", "")
    hosts = [host.strip() for host in hosts_env.split(",") if host.strip()]
    if settings.debug:
        # Allow localhost and 127.0.0.1 in debug mode
        debug_hosts = ["localhost", "127.0.0.1"]
        for host in debug_hosts:
            if host not in hosts:
                hosts.append(host)
    return hosts

# Custom route class for logging and timing
class LoggingRoute(APIRoute):
    def get_route_handler(self) -> callable:
        original_route_handler = super().get_route_handler()
        async def custom_route_handler(request: Request) -> Response:
            request_id = str(uuid.uuid4())
            request.state.request_id = request_id
            start_time = time.time()
            
            # Log request
            client_host = request.client.host if request.client else "unknown"
            logger.info(
                f"Request started: {request_id} {request.method} {request.url.path} "
                f"from {client_host}"
            )
            
            # Process request
            try:
                response: Response = await original_route_handler(request)
            except Exception as e:
                # Log exception
                process_time = time.time() - start_time
                logger.error(
                    f"Request failed: {request_id} {request.method} {request.url.path} "
                    f"after {process_time:.2f}s with error: {e}",
                    exc_info=settings.debug
                )
                raise e
            
            # Log response
            process_time = time.time() - start_time
            logger.info(
                f"Request completed: {request_id} {request.method} {request.url.path} "
                f"status={response.status_code} took {process_time:.2f}s"
            )
            
            # Add correlation ID to response headers
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time"] = str(process_time)
            return response
        return custom_route_handler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info(f"Starting {settings.app_name} version {settings.version if hasattr(settings, 'version') else '0.1.0'}...")
    
    # Configuration validation
    logger.info("Validating configuration...")
    
    # Fail closed: production must never run with a placeholder or weak signing key.
    if not settings.debug and (
        settings.secret_key == "change-me-in-production"
        or len(settings.secret_key.encode("utf-8")) < 32
    ):
        raise RuntimeError(
            "Unsafe SECRET_KEY: configure a unique random secret of at least 32 bytes "
            "before starting with DEBUG=false."
        )
    
    # Validate database connection
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            logger.info("Database connection verified.")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        if not settings.debug:
            raise RuntimeError(f"Cannot start application without database connection: {e}")
        else:
            logger.warning("Starting in debug mode despite database connection failure.")
    
    # Validate required directories
    uploads_dir = "uploads"
    if not os.path.exists(uploads_dir):
        try:
            os.makedirs(uploads_dir)
            logger.info(f"Created uploads directory: {uploads_dir}")
        except Exception as e:
            logger.error(f"Failed to create uploads directory: {e}")
    
    # Initialize Redis cache
    try:
        await cache_service.connect()
        logger.info("Redis cache connected successfully.")
    except Exception as e:
        logger.warning(f"Failed to connect to Redis cache: {e}")
        logger.warning("Application will continue without caching.")
    
    # Initialize Sentry if DSN is configured
    sentry_dsn = os.getenv("SENTRY_DSN")
    if sentry_dsn and not settings.debug:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
            sentry_sdk.init(
                dsn=sentry_dsn,
                traces_sample_rate=0.1,
                _experiments={"enable_logs": True},
            )
            app.add_middleware(SentryAsgiMiddleware)
            logger.info("Sentry error tracking initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Sentry: {e}")
    
    # Initialize app info for Prometheus metrics
    init_app_info(
        version=getattr(settings, 'version', "0.1.0"),
        environment="production" if not settings.debug else "development"
    )
    
    # Initialize database metrics (placeholder - would need periodic updates in production)
    from app.core.metrics import DATABASE_CONNECTIONS_ACTIVE
    # In a real implementation, we would update this gauge periodically based on pool status
    DATABASE_CONNECTIONS_ACTIVE.set(0)  # Placeholder
    
    # Log startup complete
    logger.info(f"{settings.app_name} startup complete.")
    
    # Start automatic backup scheduler
    backup_interval_hours = getattr(settings, 'backup_interval_hours', 6)
    if backup_interval_hours > 0:
        logger.info(f"Starting automatic backup scheduler (interval: {backup_interval_hours} hours)")
        async def backup_scheduler():
            while True:
                try:
                    backup_service.create_backup()
                    logger.info("Automatic backup completed successfully")
                except Exception as e:
                    logger.error(f"Automatic backup failed: {e}")
                    # Optionally alert via Sentry or other
                    if sentry_dsn and not settings.debug:
                        try:
                            import sentry_sdk
                            sentry_sdk.capture_exception(e)
                        except Exception:
                            pass
                await asyncio.sleep(backup_interval_hours * 3600)
        task = asyncio.create_task(backup_scheduler())
        # Store task on app for cleanup
        app.state.backup_task = task
    
    # Start automatic data deletion scheduler
    data_deletion_interval_hours = getattr(settings, 'data_deletion_interval_hours', 24)
    data_deletion_enabled = getattr(settings, 'data_deletion_enabled', True)
    if data_deletion_enabled and data_deletion_interval_hours > 0:
        logger.info(f"Starting automatic data deletion scheduler (interval: {data_deletion_interval_hours} hours)")
        async def data_deletion_scheduler():
            from app.services.data_deletion import delete_expired_data
            from app.core.database import SessionLocal
            while True:
                try:
                    # Create a database session for the deletion process
                    db = SessionLocal()
                    try:
                        result = delete_expired_data(db, dry_run=False)
                        logger.info(f"Automatic data deletion completed: {result['deleted_count']} records processed")
                        if result['errors']:
                            logger.warning(f"Data deletion encountered {len(result['errors'])} errors")
                    finally:
                        db.close()
                except Exception as e:
                    logger.error(f"Automatic data deletion failed: {e}")
                    # Optionally alert via Sentry or other
                    if sentry_dsn and not settings.debug:
                        try:
                            import sentry_sdk
                            sentry_sdk.capture_exception(e)
                        except Exception:
                            pass
                await asyncio.sleep(data_deletion_interval_hours * 3600)
        task = asyncio.create_task(data_deletion_scheduler())
        # Store task on app for cleanup
        app.state.data_deletion_task = task
    
    yield
    
    # Shutdown
    logger.info(f"Shutting down {settings.app_name}...")
    
    # Shutdown backup scheduler
    if hasattr(app.state, 'backup_task'):
        app.state.backup_task.cancel()
        try:
            await app.state.backup_task
        except asyncio.CancelledError:
            pass
        logger.info("Automatic backup scheduler stopped.")
    
    # Shutdown data deletion scheduler
    if hasattr(app.state, 'data_deletion_task'):
        app.state.data_deletion_task.cancel()
        try:
            await app.state.data_deletion_task
        except asyncio.CancelledError:
            pass
        logger.info("Automatic data deletion scheduler stopped.")
    
    # Close database connections
    try:
        # SQLAlchemy engine doesn't need explicit closing in most cases
        # but we can dispose if we have connection pool issues
        # engine.dispose()
        logger.info("Database connections closed.")
    except Exception as e:
        logger.error(f"Error closing database connections: {e}")
    
    logger.info("Shutdown complete.")
    
    # Close Redis cache connection
    try:
        await cache_service.disconnect()
        logger.info("Redis cache connection closed.")
    except Exception as e:
        logger.warning(f"Error closing Redis cache connection: {e}")

# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=getattr(settings, 'version', "0.1.0"),
    description="SolenOS: Trust-first living care record",
    lifespan=lifespan,
    # Disable docs in production if desired
    # docs_url=None if not settings.debug else "/docs",
    # redoc_url=None if not settings.debug else "/redoc",
)

# Set custom route class for logging and timing
app.router.route_class = LoggingRoute

# Middleware (order matters!)
# CORS - must be first to handle preflight requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Report-ID", "X-Process-Time"],
)

# Trusted Host - validate incoming requests
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=get_trusted_hosts(),
)

# GZip compression for responses
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Rate limiting middleware
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Skip rate limiting for health checks and docs
    if request.url.path in ["/health", "/ping", "/ready", "/info", "/version", "/docs", "/redoc", "/openapi.json"]:
        return await call_next(request)
    
    client_id = get_client_identifier(request)
    path = request.url.path
    
    # Determine which limiter to use based on path
    if path.startswith("/api/v1/auth") or path.startswith("/auth"):
        limiter = auth_limiter
        limiter_type = "auth"
    elif path.startswith("/api/v1") or path.startswith("/api/"):
        limiter = api_limiter
        limiter_type = "api"
    else:
        # Default limiter for other endpoints
        limiter = api_limiter
        limiter_type = "api"
    
    # Create a key that combines client ID and endpoint pattern to limit per-endpoint
    # For simplicity, we'll use client ID + general limiter for now
    # In a more sophisticated implementation, we might have different limits per endpoint
    if not limiter.is_allowed(client_id):
        # Send security alert for rate limit exceeded
        send_security_alert(
            event_type="rate_limit_exceeded",
            description=f"Rate limit exceeded for {limiter_type} endpoint: {path}",
            severity="warning",
            source_ip=client_id,
            details={
                "path": path,
                "method": request.method,
                "limiter_type": limiter_type,
                "limit": limiter.requests_per_window,
                "window_seconds": limiter.window_seconds,
                "client_id": client_id
            }
        )
        
        return JSONResponse(
            status_code=429,
            content=ErrorResponse(
                error="Rate limit exceeded",
                error_code="RATE_LIMIT_EXCEEDED",
                details={"limit": limiter.requests_per_window, "window_seconds": limiter.window_seconds},
            ).model_dump(),
        )
    
    response: Response = await call_next(request)
    return response

# HTTP monitoring middleware
@app.middleware("http")
async def http_monitoring_middleware(request: Request, call_next):
    """Monitor HTTP requests for Prometheus metrics."""

    method = request.method
    endpoint = request.url.path
    
    start_time = time.time()
    try:
        response = await call_next(request)
        http_status = response.status_code
        # Record metrics
        from app.core.metrics import HTTP_REQUESTS_TOTAL, HTTP_REQUEST_DURATION_SECONDS
        HTTP_REQUESTS_TOTAL.labels(
            method=method,
            endpoint=endpoint,
            http_status=str(http_status)
        ).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(
            method=method,
            endpoint=endpoint
        ).observe(time.time() - start_time)
        return response
    except Exception as e:
        # Record error metrics
        from app.core.metrics import HTTP_REQUESTS_TOTAL, HTTP_REQUEST_DURATION_SECONDS, ERRORS_TOTAL
        HTTP_REQUESTS_TOTAL.labels(
            method=method,
            endpoint=endpoint,
            http_status="500"
        ).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(
            method=method,
            endpoint=endpoint
        ).observe(time.time() - start_time)
        ERRORS_TOTAL.labels(
            error_type=type(e).__name__,
            component="http"
        ).inc()
        raise

# Security headers middleware
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response: Response = await call_next(request)
    return add_security_headers(request, response)

# Request size limiting middleware
@app.middleware("http")
async def report_size_limit_middleware(request: Request, call_next):
    # Check content-length header
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            length = int(content_length)
            # Limit to 10MB by default
            max_size = 10 * 1024 * 1024  # 10MB
            if length > max_size:
                return JSONResponse(
                    status_code=413,
                    content=ErrorResponse(
                        error="Payload too large",
                        error_code="PAYLOAD_TOO_LARGE",
                        details={"max_size_mb": max_size // (1024 * 1024)},
                    ).model_dump(),
                )
        except ValueError:
            pass  # Invalid header, let downstream handle it
    
    response: Response = await call_next(request)
    return response

# HTTP method validation middleware
@app.middleware("http")
async def http_method_validation_middleware(request: Request, call_next):
    allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    if request.method not in allowed_methods:
        return JSONResponse(
        status_code=405,
        content=ErrorResponse(
            error=f"Method {request.method} not allowed",
            error_code="METHOD_NOT_ALLOWED",
            details={"allowed_methods": list(allowed_methods)},
        ).model_dump(),
    )
    
    response: Response = await call_next(request)
    return response

# Exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    request_id = getattr(request.state, "request_id", "unknown")
    error_response = ErrorResponse(
        error=exc.detail,
        error_code=f"HTTP_{exc.status_code}",
            details={"report_id": request_id},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.model_dump(),
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = getattr(request.state, "request_id", "unknown")
    
    # Send security alert for validation errors (could indicate malicious input)
    send_security_alert(
        event_type="validation_error",
        description=f"Validation error on {request.method} {request.url.path}",
        severity="warning",
        source_ip=get_client_identifier(request),
        details={
            "path": request.url.path,
            "method": request.method,
            "request_id": request_id,
            "errors": exc.errors(),
            "client_id": get_client_identifier(request)
        }
    )
    
    error_response = ErrorResponse(
        error="Validation error",
        error_code="VALIDATION_ERROR",
        details={
            "report_id": request_id,
            "errors": exc.errors(),
        },
    )
    return JSONResponse(
        status_code=422,
        content=error_response.model_dump(),
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    
    # Send security alert for unhandled exceptions (could indicate attack or serious bug)
    send_security_alert(
        event_type="unhandled_exception",
        description=f"Unhandled exception in {request.method} {request.url.path}: {type(exc).__name__}",
        severity="critical",
        source_ip=get_client_identifier(request),
        details={
            "path": request.url.path,
            "method": request.method,
            "request_id": request_id,
            "exception": str(exc),
            "exception_type": type(exc).__name__,
            "client_id": get_client_identifier(request)
        }
    )
    
    if settings.debug:
        # In debug mode, we can return more details (but still be cautious)
        error_response = ErrorResponse(
            error="Internal server error",
            error_code="INTERNAL_SERVER_ERROR",
            details={
                "report_id": request_id,
                "debug": {
                    "exception": str(exc),
                    "type": type(exc).__name__,
                },
            },
        )
    else:
        error_response = ErrorResponse(
            error="Internal server error",
            error_code="INTERNAL_SERVER_ERROR",
            details={"report_id": request_id},
        )
    return JSONResponse(
        status_code=500,
        content=error_response.model_dump(),
    )

# Include all routers with appropriate prefixes
app.include_router(situation_router)  # situation router has prefix="/api", so it becomes "/api/situation"
app.include_router(v1_router, prefix="/api/v1")
app.include_router(notification_router, prefix="/api/v1")
app.include_router(caregiver_intelligence_router, prefix="/api/v1")
app.include_router(change_router, prefix="/api/v1")
app.include_router(care_summary_router, prefix="/api/v1")
app.include_router(care_graph_router, prefix="/api/v1")
app.include_router(companion_router, prefix="/api/v1")
app.include_router(user_router, prefix="/api/v1")
app.include_router(weekly_review_router, prefix="/api/v1")
app.include_router(wellbeing_router, prefix="/api/v1")
app.include_router(what_matters_router, prefix="/api/v1")
app.include_router(what_should_i_do_router, prefix="/api/v1")
app.include_router(dementia_router, prefix="/api/v1")
app.include_router(files_router, prefix="/api/v1/files")
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(daily_capture_router, prefix="/api/v1")
app.include_router(daily_care_router, prefix="/api/v1")
app.include_router(activation_router, prefix="/api/v1")
app.include_router(job_router, prefix="/api/v1")  # Job management endpoints
app.include_router(reasoning_router, prefix="/reasoning")
app.include_router(uncertainty_router, prefix="/api/v1")
app.include_router(knowledge_revision_router, prefix="/api/v1")
app.include_router(safety_router, prefix="/api/v1")
app.include_router(uncertainty_router, prefix="/api/v1")
app.include_router(knowledge_revision_router, prefix="/api/v1")
app.include_router(safety_router, prefix="/api/v1")

# Root endpoints
@app.get("/", tags=["root"])
async def root():
    return {
        "app": settings.app_name,
        "version": getattr(settings, 'version', "0.1.0"),
        "status": "running",
        "docs": "/docs",
        "api": "/api/v1",
        "role": "api",
        "environment": "development" if settings.debug else "production",
    }

@app.get("/health", tags=["monitoring"])
def health_check():
    """Enhanced liveness probe - checks if the application is running."""
    try:
        # Basic application info
        health_info = {
            "status": "healthy",
            "app": settings.app_name,
            "version": getattr(settings, "version", "0.1.0"),
            "timestamp": time.time(),
            "environment": "development" if settings.debug else "production"
        }
        
        # Add uptime if we have a start time (would need to be stored globally)
        # For now, we just return the basic info
        logger.debug("Health check performed", extra=health_info)
        return health_info
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "unhealthy", "error": str(e)}
        )
@app.get("/ping", tags=["monitoring"])
def ping():
    """Simple liveness check - lighter than health check."""
    return PlainTextResponse("OK")

@app.get("/ready", tags=["monitoring"])
def readiness_check():
    """
    Readiness probe - checks critical dependencies.
    Returns 200 if all dependencies are healthy, 503 otherwise.
    """
    checks = {}
    overall_status = "ready"
    
    # Check database
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"failed: {e}"
        overall_status = "not ready"
    
    # Check uploads directory
    uploads_dir = "uploads"
    if os.path.exists(uploads_dir) and os.access(uploads_dir, os.W_OK):
        checks["uploads_directory"] = "ok"
    else:
        checks["uploads_directory"] = "failed: directory missing or not writable"
        overall_status = "not ready"
    
    # In a real application, we might check other dependencies (e.g., external services)
    # For now, we check database and filesystem.
    
    if overall_status == "ready":
        return {"status": "ready", "checks": checks}
    else:
        # Return 503 Service Unavailable
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "checks": checks},
        )

@app.get("/info", tags=["monitoring"])
def app_info():
    """Application information endpoint."""
    return {
        "name": settings.app_name,
        "version": getattr(settings, 'version', "0.1.0"),
        "description": "SolenOS: Trust-first living care record",
        "environment": "development" if settings.debug else "production",
        "debug": settings.debug,
        "python_version": os.sys.version,
    }

@app.get("/version", tags=["monitoring"])
def version():
    """Version information endpoint."""
    return {
        "version": getattr(settings, 'version', "0.1.0"),
        "api_version": "v1",
    }


@app.get("/metrics", tags=["monitoring"])
def metrics():
    """Prometheus metrics endpoint."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# Serve OpenAPI spec directly (useful for API gateways)
@app.get("/api/v1/openapi.json", include_in_schema=False)
def get_openapi_json():
    return app.openapi()

# Add a custom route to list all routes (for debugging)
if settings.debug:
    @app.get("/routes", include_in_schema=False)
    def list_routes():
        """List all registered routes (debug only)."""
        routes = []
        for report in app.routes:
            if hasattr(report, "path"):
                routes.append({
                    "path": report.path,
                    "name": getattr(report, "name", "unknown"),
                    "methods": getattr(report, "methods", []),
                })
        return {"routes": routes}

logger.info("SolenOS backend application initialized successfully.")

