"""
Cache decorators and utilities for SolenOS API endpoints.
Provides easy-to-use caching decorators for automatic response caching.
"""
import asyncio
import functools
import json
import inspect
from typing import Any, Callable, Optional, Union
from fastapi import Request, Response
from app.core.cache import get_cache, set_cache
from app.core.cache_key_utils import (
    generate_cache_key_from_path,
    get_cache_ttl_for_data_type,
    CacheKeyPrefix,
    CacheTTL
)
import logging

logger = logging.getLogger(__name__)


def cached_response(
    ttl: Optional[int] = None,
    key_prefix: Optional[str] = None,
    key_func: Optional[Callable] = None,
    unless: Optional[Callable] = None,
    cache_error_responses: bool = False,
    cache_none_results: bool = True
):
    """
    Decorator to automatically cache API endpoint responses.
    
    This decorator extracts person_id and other parameters from the request
    to generate properly scoped cache keys with authorization isolation.
    
    Args:
        ttl: Time to live in seconds (if None, uses default based on key_prefix)
        key_prefix: Cache key prefix (if None, tries to infer from function name)
        key_func: Custom function to generate cache key (takes precedence over automatic)
        unless: Function that returns True if caching should be skipped
        cache_error_responses: Whether to cache error responses (non-2xx status codes)
        cache_none_results: Whether to cache None/empty results
        
    Usage:
        @cached_response(ttl=300, key_prefix="events")
        async def list_events(person_id: str, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
            # ... implementation
            
        # Or with custom key function
        @cached_response(key_func=lambda person_id, **kwargs: f"events:{person_id}:recent")
        async def list_recent_events(person_id: str, db: Session = Depends(get_db)):
            # ... implementation
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract request object if present (for getting path, query params, etc.)
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if request is None:
                # Check kwargs for request
                request = kwargs.get('request')
                
            # Determine if we should skip caching
            if unless and unless(*args, **kwargs):
                logger.debug(f"Skipping cache for {func.__name__} due to unless condition")
                # Still need to call the function properly
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)
                
            # Generate cache key
            cache_key = None
            if key_func:
                # Use custom key function
                try:
                    cache_key = key_func(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"Error generating cache key with key_func: {e}")
                    cache_key = None
            else:
                # Generate key automatically from request
                if request:
                    # Extract path and query parameters
                    path = str(request.url.path)
                    query_params = dict(request.query_params)
                    
                    # Try to extract person_id from path or query params
                    person_id = None
                    path_params = {}  # In a real implementation, we'd get these from the router
                    
                    # Try to get person_id from various sources
                    # 1. Check if it's in kwargs (from path parameters)
                    person_id = kwargs.get('person_id')
                    
                    # 2. Check query parameters
                    if not person_id:
                        person_id = query_params.get('person_id')
                        
                    # 3. Try to extract from path (simplified)
                    if not person_id and path:
                        path_parts = [p for p in path.strip('/').split('/') if p]
                        for part in path_parts:
                            if part.startswith('person-'):
                                person_id = part
                                break
                                
                    # Generate cache key
                    if key_prefix:
                        prefix = key_prefix
                    else:
                        # Try to infer prefix from function name
                        func_name = func.__name__
                        # Remove common prefixes/suffixes
                        for prefix_to_remove in ['get_', 'list_', 'create_', 'update_', 'delete_']:
                            if func_name.startswith(prefix_to_remove):
                                func_name = func_name[len(prefix_to_remove):]
                        for suffix_to_remove in ['_list', '_items', '_data', '_info']:
                            if func_name.endswith(suffix_to_remove):
                                func_name = func_name[:-len(suffix_to_remove)]
                        prefix = func_name or "endpoint"
                        
                    cache_key = generate_cache_key_from_path(
                        path=path,
                        person_id=person_id,
                        query_params=query_params,
                        path_params=path_params
                    )
                    # Ensure we have the specified prefix if provided
                    if key_prefix and not cache_key.startswith(f"{key_prefix}:"):
                        # Rebuild with proper prefix
                        cache_key = generate_cache_key(
                            prefix=key_prefix,
                            person_id=person_id,
                            query_params=query_params
                        )
                else:
                    # No request object available, create a basic key
                    func_name = func.__name__
                    cache_key = f"func:{func_name}:{hash(str(args) + str(sorted(kwargs.items())))}"
                    
            if not cache_key:
                # Fallback: execute function without caching
                logger.debug(f"No cache key generated for {func.__name__}, executing without cache")
                if asyncio.iscoroutinefunction(func):
                    return await func(*args, **kwargs)
                else:
                    return func(*args, **kwargs)
                
            # Try to get from cache
            try:
                cached_value = await get_cache(cache_key)
                if cached_value is not None:
                    logger.debug(f"Cache hit for key: {cache_key}")
                    # If it's a JSON string, parse it back to object
                    if isinstance(cached_value, str):
                        try:
                            return json.loads(cached_value)
                        except json.JSONDecodeError:
                            # Return as string if not JSON
                            return cached_value
                    return cached_value
                else:
                    logger.debug(f"Cache miss for key: {cache_key}")
            except Exception as e:
                logger.warning(f"Error reading from cache for key {cache_key}: {e}")
                # Continue to execute function
                
            # Execute the function
            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                
                # Determine if we should cache the result
                should_cache = True
                
                # Don't cache None results if configured not to
                if result is None and not cache_none_results:
                    should_cache = False
                    
                # Don't cache error responses if not configured to do so
                if hasattr(result, 'status_code') and not cache_error_responses:
                    if not (200 <= result.status_code < 300):
                        should_cache = False
                        
                if should_cache:
                    # Determine TTL
                    effective_ttl = ttl
                    if effective_ttl is None and key_prefix:
                        effective_ttl = get_cache_ttl_for_data_type(key_prefix)
                    elif effective_ttl is None:
                        effective_ttl = CacheTTL.DEFAULT
                        
                    # Serialize result for caching
                    try:
                        if isinstance(result, (dict, list, str, int, float, bool)) or result is None:
                            # JSON-serializable types
                            value_to_cache = json.dumps(result) if not isinstance(result, str) else result
                        else:
                            # For other types (including Response objects), we might want to avoid caching
                            # or handle them specially. For now, we'll skip caching complex objects.
                            logger.debug(f"Skipping cache for non-serializable result type: {type(result)}")
                            should_cache = False
                    except (TypeError, ValueError) as e:
                        logger.warning(f"Error serializing result for caching: {e}")
                        should_cache = False
                        
                    if should_cache:
                        try:
                            await set_cache(cache_key, value_to_cache, expire=effective_ttl)
                            logger.debug(f"Cached result for key: {cache_key} with TTL: {effective_ttl}s")
                        except Exception as e:
                            logger.warning(f"Error writing to cache for key {cache_key}: {e}")
                            
                return result
                
            except Exception as e:
                logger.error(f"Error executing cached function {func.__name__}: {e}")
                # Optionally cache error responses
                if cache_error_responses and cache_key:
                    try:
                        error_result = {"error": str(e), "type": type(e).__name__}
                        await set_cache(cache_key, json.dumps(error_result), expire=min(ttl or 60, 300))  # Short TTL for errors
                    except Exception:
                        pass  # Don't let error caching errors break error handling
                raise
                
        return wrapper
    return decorator


def cache_invalidate_on_update(
    *cache_key_patterns: str,
    person_id_param: str = "person_id",
    key_extractor: Optional[Callable] = None,
    unless: Optional[Callable] = None
):
    """
    Decorator to invalidate cache entries when data is updated.
    
    This should be used on POST, PUT, PATCH, DELETE endpoints that modify data.
    
    Args:
        *cache_key_patterns: Patterns of cache keys to invalidate (can include {person_id} placeholder)
        person_id_param: Name of the parameter containing person_id (used if key_extractor not provided)
        key_extractor: Function that takes (args, kwargs) and returns person_id for scoping invalidation
        unless: Function that returns True if invalidation should be skipped
        
    Usage:
        # For path/query parameters
        @cache_invalidate_on_update("events:{person_id}:*", "insights:{person_id}:*")
        async def update_event(event_id: str, person_id: str, db: Session = Depends(get_db)):
            # ... implementation
            
        # For body parameters
        @cache_invalidate_on_update(
            "events:{person_id}:*", 
            "insights:{person_id}:*",
            key_extractor=lambda args, kwargs: kwargs.get('event', {}).person_id if hasattr(kwargs.get('event', {}), 'person_id') else None
        )
        async def create_event(event: CareEventCreate, db: Session = Depends(get_db)):
            # ... implementation
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Execute the function first
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            # Determine if we should skip invalidation
            if unless and unless(*args, **kwargs):
                logger.debug(f"Skipping cache invalidation for {func.__name__} due to unless condition")
                return result
                
            # Extract person_id for scoping invalidation
            person_id = None
            if key_extractor:
                try:
                    person_id = key_extractor(args, kwargs)
                except Exception as e:
                    logger.warning(f"Error extracting person_id using key_extractor: {e}")
            elif person_id_param in kwargs:
                person_id = kwargs[person_id_param]
            else:
                # Fallback: try to find person_id in common locations
                # Check if first arg after self has person_id attribute (for request bodies)
                if args and hasattr(args[0], person_id_param):
                    person_id = getattr(args[0], person_id_param)
                # Check kwargs for objects that might have person_id
                elif kwargs:
                    for value in kwargs.values():
                        if hasattr(value, person_id_param):
                            person_id = getattr(value, person_id_param)
                            break
            
            # Invalidate cache entries matching the patterns
            try:
                from app.core.cache import cache_service
                
                for pattern in cache_key_patterns:
                    # If pattern contains {person_id} placeholder and we have person_id, replace it
                    if "{person_id}" in pattern and person_id:
                        scoped_pattern = pattern.format(person_id=person_id)
                    elif person_id and not pattern.endswith(":*") and ":" not in pattern.split(":")[-1]:
                        # Add person_id scoping if not already present (simple heuristic)
                        if not pattern.startswith(f"{person_id}:"):
                            scoped_pattern = f"{person_id}:{pattern}"
                        else:
                            scoped_pattern = pattern
                    else:
                        scoped_pattern = pattern
                        
                    # Delete matching keys
                    deleted_count = await cache_service.clear_pattern(scoped_pattern)
                    if deleted_count > 0:
                        logger.debug(f"Invalidated {deleted_count} cache keys matching pattern: {scoped_pattern}")
                    else:
                        logger.debug(f"No cache keys found matching pattern: {scoped_pattern}")
                        
            except Exception as e:
                logger.warning(f"Error invalidating cache for {func.__name__}: {e}")
                # Don't let cache invalidation errors break the main operation
                
            return result
        return wrapper
    return decorator


def cache_invalidate_on_delete(
    *cache_key_patterns: str,
    person_id_param: str = "person_id",
    key_extractor: Optional[Callable] = None,
    unless: Optional[Callable] = None
):
    """
    Decorator to invalidate cache entries when data is deleted.
    
    Similar to cache_invalidate_on_update but semantically for delete operations.
    """
    return cache_invalidate_on_update(*cache_key_patterns, person_id_param=person_id_param, key_extractor=key_extractor, unless=unless)


# Specialized decorators for common endpoint types
def cache_list_endpoint(ttl: Optional[int] = None, key_prefix: Optional[str] = None):
    """
    Decorator specifically for list endpoints (GET endpoints returning lists).
    """
    return cached_response(ttl=ttl, key_prefix=key_prefix)


def cache_entity_endpoint(ttl: Optional[int] = None, key_prefix: Optional[str] = None):
    """
    Decorator specifically for entity endpoints (GET endpoints returning single entities).
    """
    return cached_response(ttl=ttl, key_prefix=key_prefix)


def cache_create_endpoint(*invalidate_patterns: str):
    """
    Decorator specifically for create endpoints (POST endpoints).
    """
    return cache_invalidate_on_update(*invalidate_patterns)


def cache_update_endpoint(*invalidate_patterns: str):
    """
    Decorator specifically for update endpoints (PUT/PATCH endpoints).
    """
    return cache_invalidate_on_update(*invalidate_patterns)


def cache_delete_endpoint(*invalidate_patterns: str):
    """
    Decorator specifically for delete endpoints (DELETE endpoints).
    """
    return cache_invalidate_on_delete(*invalidate_patterns)


# Example usage
def _example_usage():
    """Example usage of the cache decorators."""
    print("Cache decorators module loaded successfully")
    print("Usage examples:")
    print("  @cached_response(ttl=300, key_prefix='events')")
    print("  async def list_events(person_id: str, limit: int = 50, offset: int = 0):")
    print("      # ... implementation")
    print("")
    print("  @cache_invalidate_on_update('events:*', 'insights:*')")
    print("  async def create_event(event: CareEventCreate, person_id: str, db: Session = Depends(get_db)):")
    print("      # ... implementation")


if __name__ == "__main__":
    _example_usage()
