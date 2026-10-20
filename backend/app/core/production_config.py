from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Literal
import os

class ProductionSettings(BaseSettings):
    # Override these for production
    debug: bool = False
    log_level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR'] = 'INFO'
    
    # Security
    secret_key: str = Field(..., min_length=32)  # Must be set in production
    access_token_expire_minutes: int = 60 * 24  # 24 hours
    
    # Performance
    database_pool_size: int = 20
    max_concurrent_requests: int = 100
    
    # LLM Production Settings
    llm_max_output_tokens: int = 200  # More conservative in production
    llm_circuit_failure_threshold: int = 2  # More sensitive
    llm_timeout_seconds: float = 30.0  # Shorter timeout for faster failure detection
    
    # Backup Settings
    backup_enabled: bool = True
    backup_interval_hours: int = 6
    backup_retention_days: int = 30
    backup_dir: str = './backups'
    
    # File Upload Settings
    max_upload_size: int = 10 * 1024 * 1024  # 10MB
    upload_dir: str = './uploads'
    
    # Rate Limiting
    rate_limit_auth_requests: int = 10
    rate_limit_auth_window: int = 60
    rate_limit_api_requests: int = 100
    rate_limit_api_window: int = 60
    rate_limit_strict_requests: int = 5
    rate_limit_strict_window: int = 60
    
    # Environment
    environment: Literal['development', 'staging', 'production'] = 'production'
    
    class Config:
        env_file = '.env.production'
        env_nested_delimiter = '__'
        case_sensitive = True

def get_production_settings() -> ProductionSettings:
    """Get production settings instance"""
    return ProductionSettings()
