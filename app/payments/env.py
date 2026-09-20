"""Reads required env vars, failing loudly at the point of use rather than
surfacing a confusing downstream error mid-request."""
import os


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value
