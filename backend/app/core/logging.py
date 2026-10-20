import logging
import logging.handlers
import os
import sys
import json
from datetime import datetime
from pathlib import Path
from app.core.config import get_settings


class JSONFormatter(logging.Formatter):
    """Formatter that outputs JSON strings."""

    def format(self, record):
        log_record = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "process": record.process,
            "thread": record.thread,
            "filename": record.filename,
            "lineno": record.lineno,
            "funcName": record.funcName,
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


def setup_logging():
    """Configure production-ready logging for SolenOS."""
    settings = get_settings()
    
    # Create logs directory if it doesn't exist
    log_dir = Path(getattr(settings, 'log_dir', './logs'))
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Get log file path
    log_file = Path(getattr(settings, 'log_file', log_dir / 'solenos.log'))
    
    # Get log format setting
    log_format = getattr(settings, 'log_format', 'text').lower()
    
    # Determine log level
    log_level_name = getattr(settings, 'log_level', 'INFO')
    log_level = getattr(logging, log_level_name.upper(), logging.INFO)
    
    # Create formatters
    if log_format == "json":
        formatter = JSONFormatter()
        # No need for separate simple formatter; JSON is used for both
        file_formatter = formatter
        console_formatter = formatter
    else:
        detailed_formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(process)d:%(thread)d - %(filename)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        simple_formatter = logging.Formatter(
            fmt='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%H:%M:%S'
        )
        file_formatter = detailed_formatter
        console_formatter = simple_formatter if settings.debug else detailed_formatter
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear any existing handlers
    root_logger.handlers.clear()
    
    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=getattr(settings, 'log_max_bytes', 10 * 1024 * 1024),  # 10MB
        backupCount=getattr(settings, 'log_backup_count', 5),
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)
    
    # Console handler (for development or when explicitly wanted)
    if settings.debug or getattr(settings, 'log_to_console', True):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        if settings.debug:
            console_handler.setFormatter(console_formatter)
        else:
            # In production, use detailed formatter unless JSON format
            if log_format != "json":
                console_handler.setFormatter(console_formatter)
            else:
                console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)
    
    # Suppress noisy third-party libraries in production
    if not settings.debug:
        logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
        logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
    
    # Log the logging configuration
    logger = logging.getLogger('solenos.logging')
    logger.info(f"Logging configured. Level: {log_level_name}, Format: {log_format}, File: {log_file}")
    
    return logger


# Initialize logging when module is imported
logger = setup_logging()