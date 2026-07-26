"""Local FastAPI research service."""

from .app import create_app
from .container import AppServices, create_services

__all__ = ["AppServices", "create_app", "create_services"]
