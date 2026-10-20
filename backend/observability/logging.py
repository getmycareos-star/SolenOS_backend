import logging
import sys
import uuid
from typing import Any, Dict
from contextvars import ContextVar

# Try to import jsonlogger, fallback to regular formatter if not available
try:
    from pythonjsonlogger import jsonlogger
    HAS_JSON_LOGGER = True
except ImportError:
    HAS_JSON_LOGGER = False
    jsonlogger = None

# Context variable for correlation ID
correlation_id_var: ContextVar[str] = ContextVar('correlation_id', default='')

class CorrelationIdFilter(logging.Filter):
    """Add correlation ID to log records"""
    def filter(self, record):
        record.correlation_id = correlation_id_var.get()
        return True

def setup_logging(
    level: str = "INFO",
    format_string: str = "%(timestamp)s %(level)s %(name)s %(message)s",
    enable_json: bool = True
) -> logging.Logger:
    """Setup structured logging for the application"""
    
    # Create logger
    logger = logging.getLogger("solenos")
    logger.setLevel(getattr(logging, level.upper()))
    
    # Remove existing handlers
    logger.handlers.clear()
    
    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper()))
    
    # Create formatter
    if enable_json and HAS_JSON_LOGGER:
        formatter = jsonlogger.JsonFormatter(
            '%(timestamp)s %(level)s %(name)s %(message)s %(correlation_id)s',
            timestamp_format='%Y-%m-%d %H:%M:%S'
        )
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s [correlation_id=%(correlation_id)s]'
        )
    
    handler.setFormatter(formatter)
    handler.addFilter(CorrelationIdFilter())
    
    logger.addHandler(handler)
    
    # Prevent duplicate logs
    logger.propagate = False
    
    return logger

def get_correlation_id() -> str:
    """Get current correlation ID"""
    return correlation_id_var.get()

def set_correlation_id(correlation_id: str = None) -> str:
    """Set correlation ID and return it"""
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())
    correlation_id_var.set(correlation_id)
    return correlation_id

def reset_correlation_id():
    """Reset correlation ID to default"""
    correlation_id_var.set('')

# Logger instance
logger = setup_logging()