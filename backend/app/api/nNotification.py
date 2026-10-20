"""Compatibility shim for the legacy misspelled notification module."""
from app.api.notification import router

__all__ = ["router"]
