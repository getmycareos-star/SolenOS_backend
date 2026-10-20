"""
Cache key utilities for SolenOS.
Provides functions for generating safe, authorized cache keys.
"""
import hashlib
import re
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode


def sanitize_cache_key_component(component: Any) -> str:
    """
    Sanitize a cache key component to be safe for use in Redis keys.
    
    Args:
        component: The component to sanitize (will be converted to string)
        
    Returns:
        A sanitized string safe for use in Redis keys
    """
    # Convert to string
    str_component = str(component)
    
    # Remove or replace characters that could cause issues in Redis keys
    # Redis keys are binary safe, but we want to avoid problematic characters
    # that might cause issues with monitoring, debugging, or key scanning
    sanitized = re.sub(r'[\s\n\r\t\\]', '_', str_component)
    
    # Limit length to prevent extremely long keys
    if len(sanitized) > 200:
        # Hash long components to prevent excessively long keys
        hash_suffix = hashlib.md5(str_component.encode()).hexdigest()[:8]
        sanitized = sanitized[:190] + "_" + hash_suffix
        
    return sanitized


def generate_cache_key(
    prefix: str,
    person_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    query_params: Optional[Dict[str, Any]] = None,
    user_context: Optional[str] = None,
    additional_scopes: Optional[Dict[str, Any]] = None
) -> str:
    """
    Generate a safe, authorized cache key for SolenOS resources.
    
    This function ensures proper authorization isolation by including
    person_id and other scoping parameters in the cache key.
    
    Args:
        prefix: A prefix identifying the cache category (e.g., "events", "insights")
        person_id: The person ID for authorization isolation (required for user-specific data)
        resource_type: Type of resource being cached (optional)
        resource_id: Specific resource ID (optional)
        query_params: Query parameters that affect the result (optional)
        user_context: Additional user context for authorization (optional)
        additional_scopes: Additional scoping parameters (optional)
        
    Returns:
        A properly scoped cache key string
        
    Example:
        generate_cache_key(
            prefix="events",
            person_id="person-123",
            query_params={"limit": 5, "offset": 0}
        )
        # Returns: "events:person-123:limit_5:offset_0"
    """
    # Start with the prefix
    key_parts = [sanitize_cache_key_component(prefix)]
    
    # Add person_id for authorization isolation (if provided)
    if person_id is not None:
        key_parts.append(sanitize_cache_key_component(person_id))
    
    # Add resource type and ID if provided
    if resource_type is not None:
        key_parts.append(sanitize_cache_key_component(resource_type))
    if resource_id is not None:
        key_parts.append(sanitize_cache_key_component(resource_id))
        
    # Add user context if provided (for additional authorization layers)
    if user_context is not None:
        key_parts.append(sanitize_cache_key_component(user_context))
        
    # Add additional scopes
    if additional_scopes:
        for key, value in sorted(additional_scopes.items()):
            key_parts.append(sanitize_cache_key_component(key))
            key_parts.append(sanitize_cache_key_component(value))
            
    # Add query parameters (sorted for consistency)
    if query_params:
        # Filter out None values and sort by key for consistent key generation
        filtered_params = {
            k: v for k, v in query_params.items() 
            if v is not None and k not in ['callback', '_']  # Exclude common junk params
        }
        if filtered_params:
            # Sort by key to ensure consistent ordering
            sorted_items = sorted(filtered_params.items())
            query_string = urlencode(sorted_items)
            # Sanitize the query string to be safe for cache keys
            sanitized_query = sanitize_cache_key_component(query_string)
            key_parts.append(sanitized_query)
    
    # Join all parts with colons
    cache_key = ":".join(key_parts)
    
    # Ensure the key is not empty and has reasonable length
    if not cache_key or len(cache_key) > 500:
        # Fallback to a hashed key if something went wrong
        fallback_data = f"{prefix}:{person_id or 'no_person'}:{resource_type or ''}:{resource_id or ''}"
        fallback_hash = hashlib.md5(fallback_data.encode()).hexdigest()
        cache_key = f"cache:{fallback_hash}"
        
    return cache_key


def generate_list_cache_key(
    prefix: str,
    person_id: str,
    limit: int = 50,
    offset: int = 0,
    filters: Optional[Dict[str, Any]] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None
) -> str:
    """
    Generate a cache key for list endpoints with pagination and filtering.
    
    Args:
        prefix: Cache prefix (e.g., "events", "insights")
        person_id: Person ID for authorization isolation
        limit: Number of items to return
        offset: Number of items to skip
        filters: Filter parameters
        sort_by: Field to sort by
        sort_order: Sort order (asc/desc)
        
    Returns:
        A cache key for the list query
    """
    query_params = {
        "limit": limit,
        "offset": offset
    }
    
    if filters:
        query_params.update(filters)
    if sort_by:
        query_params["sort_by"] = sort_by
    if sort_order:
        query_params["sort_order"] = sort_order
        
    return generate_cache_key(
        prefix=prefix,
        person_id=person_id,
        query_params=query_params
    )


def generate_entity_cache_key(
    prefix: str,
    person_id: str,
    entity_id: str
) -> str:
    """
    Generate a cache key for a specific entity.
    
    Args:
        prefix: Cache prefix (e.g., "event", "insight")
        person_id: Person ID for authorization isolation
        entity_id: Entity ID
        
    Returns:
        A cache key for the specific entity
    """
    return generate_cache_key(
        prefix=prefix,
        person_id=person_id,
        resource_id=entity_id
    )


def generate_aggregation_cache_key(
    prefix: str,
    person_id: str,
    aggregation_type: str,
    time_period: Optional[str] = None,
    group_by: Optional[str] = None
) -> str:
    """
    Generate a cache key for aggregation/analytic queries.
    
    Args:
        prefix: Cache prefix (e.g., "analytics", "summary")
        person_id: Person ID for authorization isolation
        aggregation_type: Type of aggregation (e.g., "daily_stats", "trend_analysis")
        time_period: Time period for aggregation (optional)
        group_by: Field to group by (optional)
        
    Returns:
        A cache key for the aggregation query
    """
    additional_scopes = {}
    if time_period:
        additional_scopes["time_period"] = time_period
    if group_by:
        additional_scopes["group_by"] = group_by
        
    return generate_cache_key(
        prefix=prefix,
        person_id=person_id,
        resource_type=aggregation_type,
        additional_scopes=additional_scopes if additional_scopes else None
    )


def generate_cache_key_from_path(
    path: str,
    person_id: Optional[str] = None,
    query_params: Optional[Dict[str, Any]] = None,
    path_params: Optional[Dict[str, Any]] = None
) -> str:
    """
    Generate a cache key from an HTTP request path and parameters.
    
    This is useful for automatically generating cache keys from API endpoints.
    
    Args:
        path: The HTTP request path (e.g., "/api/v1/events/person-123")
        person_id: Person ID for authorization (extracted from path if not provided)
        query_params: Query parameters from the request
        path_params: Path parameters extracted from routing
        
    Returns:
        A cache key suitable for the request
    """
    # Clean the path
    clean_path = path.strip("/")
    if not clean_path:
        clean_path = "root"
        
    # Split path into components
    path_components = [comp for comp in clean_path.split("/") if comp]
    
    # Extract person_id from path if not provided and if it looks like a person ID
    if person_id is None and path_params:
        person_id = path_params.get("person_id")
    elif person_id is None:
        # Try to extract person_id from common path patterns
        # Look for patterns like /api/v1/{resource}/person-123 or /person-123/{resource}
        for i, component in enumerate(path_components):
            if component.startswith("person-") and len(component) > 7:
                person_id = component
                break
    
    # Generate cache key
    return generate_cache_key(
        prefix="api",
        person_id=person_id,
        resource_type="_".join(path_components[:-1]) if len(path_components) > 1 and person_id in path_components else None,
        resource_id=path_components[-1] if path_components and person_id not in path_components else None,
        query_params=query_params,
        additional_scopes=path_params
    )


# Cache key prefixes for different data types
class CacheKeyPrefix:
    """Standardized cache key prefixes for different data types."""
    PERSON = "person"
    EVENTS = "events"
    INSIGHTS = "insights"
    OBSERVATIONS = "observations"
    EVIDENCE = "evidence"
    CAREGIVERS = "caregivers"
    CORRECTIONS = "corrections"
    REASONING_MEMORY = "reasoning_memory"
    LEARNING_EVENTS = "learning_events"
    CARE_WINDOWS = "care_windows"
    APPOINTMENTS = "appointments"
    CARE_TRANSITIONS = "care_transitions"
    LOCATIONS = "locations"
    TIMEZONES = "timezones"
    TEMPORAL_FACTS = "temporal_facts"
    TEMPORAL_RELATIONS = "temporal_relations"
    TEMPORAL_REFERENCE_FRAMES = "temporal_reference_frames"
    DAILY_INTELLIGENCE = "daily_intelligence"
    ANALYTICS = "analytics"
    SUMMARY = "summary"
    
    # Specialized caches
    LLM_REASONING = "llm_reasoning"
    AI_COMPANION = "ai_companion"
    COMMUNITY = "community"


# Cache TTL (time-to-live) constants in seconds
class CacheTTL:
    """Standard cache TTL values for different data types."""
    # Short-lived data (seconds to minutes)
    REALTIME = 30          # 30 seconds
    SHORT_TERM = 300       # 5 minutes
    MEDIUM_TERM = 1800     # 30 minutes
    
    # Medium-lived data (minutes to hours)
    LONG_TERM = 3600       # 1 hour
    EXTENDED_TERM = 7200   # 2 hours
    
    # Long-lived data (hours to days)
    DAILY = 86400          # 24 hours
    WEEKLY = 604800        # 7 days
    
    # Very long-lived or static data
    STATIC = 2592000       # 30 days
    
    # Specialized TTLs
    LLM_REASONING = 1800   # 30 minutes for LLM reasoning results
    AI_COMPANION = 3600    # 1 hour for AI companion data
    COMMUNITY_DATA = 86400 # 24 hours for community data
    
    # Default fallback
    DEFAULT = 1800         # 30 minutes


def get_cache_ttl_for_data_type(data_type: str) -> int:
    """
    Get the appropriate cache TTL for a given data type.
    
    Args:
        data_type: The type of data being cached
        
    Returns:
        TTL in seconds
    """
    ttl_map = {
        # Events and time-sensitive data
        "events": CacheTTL.SHORT_TERM,
        "observations": CacheTTL.SHORT_TERM,
        "care_windows": CacheTTL.MEDIUM_TERM,
        
        # Insights and analytical data (medium term)
        "insights": CacheTTL.MEDIUM_TERM,
        "reasoning_memory": CacheTTL.MEDIUM_TERM,
        "learning_events": CacheTTL.MEDIUM_TERM,
        
        # Entity data that changes infrequently
        "evidence": CacheTTL.LONG_TERM,
        "caregivers": CacheTTL.LONG_TERM,
        "corrections": CacheTTL.LONG_TERM,
        "appointments": CacheTTL.LONG_TERM,
        "care_transitions": CacheTTL.LONG_TERM,
        "locations": CacheTTL.STATIC,  # Rarely changes
        "timezones": CacheTTL.STATIC,  # Rarely changes
        
        # Temporal data (depends on volatility)
        "temporal_facts": CacheTTL.LONG_TERM,
        "temporal_relations": CacheTTL.LONG_TERM,
        "temporal_reference_frames": CacheTTL.STATIC,
        
        # Specialized data
        "daily_intelligence": CacheTTL.DAILY,
        "llm_reasoning": CacheTTL.LLM_REASONING,
        "ai_companion": CacheTTL.AI_COMPANION,
        "community": CacheTTL.COMMUNITY_DATA,
        
        # Analytics and summaries
        "analytics": CacheTTL.EXTENDED_TERM,
        "summary": CacheTTL.DAILY,
    }
    
    return ttl_map.get(data_type.lower(), CacheTTL.DEFAULT)


# Example usage and testing functions
def _example_usage():
    """Example usage of the cache key utilities."""
    # Generate a cache key for events list
    events_key = generate_list_cache_key(
        prefix=CacheKeyPrefix.EVENTS,
        person_id="person-123",
        limit=5,
        offset=0
    )
    print(f"Events list cache key: {events_key}")
    
    # Generate a cache key for a specific event
    event_key = generate_entity_cache_key(
        prefix=CacheKeyPrefix.EVENTS,
        person_id="person-123",
        entity_id="event-456"
    )
    print(f"Specific event cache key: {event_key}")
    
    # Generate a cache key for insights with filters
    insights_key = generate_list_cache_key(
        prefix=CacheKeyPrefix.INSIGHTS,
        person_id="person-123",
        limit=3,
        offset=0,
        filters={"is_active": True},
        sort_by="created_at",
        sort_order="desc"
    )
    print(f"Insights list cache key: {insights_key}")
    
    # Get TTL for events
    events_ttl = get_cache_ttl_for_data_type("events")
    print(f"TTL for events: {events_ttl} seconds ({events_ttl//60} minutes)")


if __name__ == "__main__":
    _example_usage()