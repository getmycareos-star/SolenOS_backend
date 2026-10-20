import time
import uuid
from typing import Dict, Any, Optional
from contextvars import ContextVar
from observability.logging import logger

# Context variables for tracing
trace_id_var: ContextVar[str] = ContextVar('trace_id', default='')
span_id_var: ContextVar[str] = ContextVar('span_id', default='')
parent_span_id_var: ContextVar[Optional[str]] = ContextVar('parent_span_id', default=None)

class TraceContext:
    """Trace context for tracking spans"""
    
    def __init__(self, trace_id: str = None, span_id: str = None, parent_span_id: str = None):
        self.trace_id = trace_id or str(uuid.uuid4())
        self.span_id = span_id or str(uuid.uuid4())
        self.parent_span_id = parent_span_id
        self.start_time = time.time()
        self.attributes: Dict[str, Any] = {}
    
    def set_attribute(self, key: str, value: Any):
        """Set an attribute on the current span"""
        self.attributes[key] = value
    
    def get_attributes(self) -> Dict[str, Any]:
        """Get all attributes"""
        return self.attributes.copy()
    
    def finish(self) -> Dict[str, Any]:
        """Finish the span and return trace data"""
        end_time = time.time()
        duration_ms = (end_time - self.start_time) * 1000
        
        trace_data = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time,
            "end_time": end_time,
            "duration_ms": duration_ms,
            "attributes": self.attributes
        }
        
        logger.debug("Span finished", 
                    extra={
                        "trace_id": self.trace_id,
                        "span_id": self.span_id,
                        "duration_ms": duration_ms,
                        "attributes": self.attributes
                    })
        
        return trace_data

def generate_trace_id() -> str:
    """Generate a new trace ID"""
    return str(uuid.uuid4())

def generate_span_id() -> str:
    """Generate a new span ID"""
    return str(uuid.uuid4())

def set_trace_context(trace_id: str = None, span_id: str = None, parent_span_id: str = None):
    """Set trace context variables"""
    if trace_id is None:
        trace_id = generate_trace_id()
    if span_id is None:
        span_id = generate_span_id()
    
    trace_id_var.set(trace_id)
    span_id_var.set(span_id)
    parent_span_id_var.set(parent_span_id)
    
    return TraceContext(trace_id, span_id, parent_span_id)

def get_trace_id() -> str:
    """Get current trace ID"""
    return trace_id_var.get()

def get_span_id() -> str:
    """Get current span ID"""
    return span_id_var.get()

def get_parent_span_id() -> Optional[str]:
    """Get current parent span ID"""
    return parent_span_id_var.get()

def get_current_trace_context() -> TraceContext:
    """Get current trace context"""
    return TraceContext(
        trace_id=get_trace_id(),
        span_id=get_span_id(),
        parent_span_id=get_parent_span_id()
    )

def with_span(name: str):
    """Decorator to create a span around a function"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Create child span
            parent_context = get_current_trace_context()
            ctx = set_trace_context(
                trace_id=parent_context.trace_id,
                parent_span_id=parent_context.span_id
            )
            ctx.set_attribute("function.name", func.__name__)
            ctx.set_attribute("function.name", name)
            
            try:
                result = func(*args, **kwargs)
                ctx.set_attribute("status", "success")
                return result
            except Exception as e:
                ctx.set_attribute("status", "error")
                ctx.set_attribute("error.message", str(e))
                raise
            finally:
                ctx.finish()
        return wrapper
    return decorator

# Initialize root trace context on module import
set_trace_context()