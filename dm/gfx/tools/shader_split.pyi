"""Dynamic public API exposed by shader_split.py to its format-specific test modules.

The scanner intentionally assembles some readers and constants at runtime from
profile-selected helpers; this stub keeps those dynamic module attributes typed.
"""
from typing import Any

def __getattr__(name: str) -> Any: ...
