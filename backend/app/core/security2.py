"""Compatibility shim for legacy imports.

Security primitives are maintained only in :mod:`app.core.security`.
"""
from app.core.security import *  # noqa: F401,F403
