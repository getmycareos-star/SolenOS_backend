import os
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from observability.logging import logger

def init_sentry():
    """Initialize Sentry error tracking"""
    sentry_dsn = os.getenv("SENTRY_DSN")
    environment = os.getenv("SENTRY_ENVIRONMENT", "development")
    release = os.getenv("SENTRY_RELEASE", "solenos@0.1.0")
    
    if not sentry_dsn:
        logger.warning("SENTRY_DSN not set, Sentry error tracking disabled")
        return
    
    sentry_sdk.init(
        dsn=sentry_dsn,
        environment=environment,
        release=release,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
        ],
        # Set traces_sample_rate to 1.0 to capture 100% of transactions for performance monitoring
        # Adjust this value in production to control overhead
        traces_sample_rate=0.1,
        # Set profiles_sample_rate to profile 10% of sampled transactions
        profiles_sample_rate=0.1,
        # Don't send PII (Personally Identifiable Information) unless explicitly configured
        send_default_pii=False,
        # Attach stack traces to messages
        attach_stacktrace=True,
        # Enable breadcrumbs based on logging
        enable_breadcrumbs=True,
        breadcrumb_logger=("logging", "sentry_sdk"),
    )
    
    logger.info(f"Sentry initialized for environment: {environment}")

