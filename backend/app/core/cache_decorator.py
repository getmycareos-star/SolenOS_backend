from functools import wraps  
from typing import Callable  
  
def cached_response(ttl: int, key_prefix: str):  
    \" \\Simple cache response decorator for testing "purposes.\\\  
    def decorator(func: Callable):  
        @wraps(func)  
        async def wrapper(*args, **kwargs):  
            # For now, just call the function without caching  
            # In a real implementation, this would check cache first  
            return await func(*args, **kwargs)  
        return wrapper  
    return decorator 
