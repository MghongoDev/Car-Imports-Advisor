"""Routers: ``api`` serves JSON, ``pages`` serves Jinja2 HTML."""

from app.routers import api, pages  # noqa: F401 pylint: disable=unused-import

__all__ = ["api", "pages"]
