"""Backward-compatible import path for the canonical cache decorator.

All cache policy and key generation live in :mod:`app.core.cache_decorators`.
Keeping this module as a shim avoids maintaining a second, silently-disabled
cache implementation.
"""
from app.core.cache_decorators import cached_response

__all__ = ["cached_response"]
