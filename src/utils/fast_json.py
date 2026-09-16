"""
D87: Fast JSON wrapper using orjson with stdlib fallback.

orjson is ~3-10x faster than stdlib json for both loads and dumps.
Key differences from stdlib json:
  - orjson.dumps() returns bytes, not str
  - orjson.dumps() does not support `default=str` kwarg directly;
    use OPT_NON_STR_KEYS | OPT_PASSTHROUGH_DATETIME etc.
  - orjson.loads() accepts both str and bytes

This module provides a consistent interface that always returns str
from dumps() for drop-in compatibility with existing code.
"""

from __future__ import annotations

import json as _stdlib_json

# Always expose JSONDecodeError for `except json.JSONDecodeError` compatibility
JSONDecodeError = _stdlib_json.JSONDecodeError

try:
    import orjson

    def loads(data: str | bytes) -> dict | list:
        """Parse JSON string/bytes to Python object (orjson)."""
        return orjson.loads(data)

    def dumps(obj: object, *, indent: bool = False, default: object = None) -> str:
        """Serialize Python object to JSON string (orjson).

        Returns str for drop-in stdlib compatibility.
        """
        opts = orjson.OPT_NON_STR_KEYS
        if indent:
            opts |= orjson.OPT_INDENT_2
        # orjson handles datetime/date/uuid natively.
        # For other non-serializable types, use default callback.
        if default is not None:
            return orjson.dumps(obj, option=opts, default=default).decode("utf-8")
        return orjson.dumps(obj, option=opts).decode("utf-8")

    def dumps_bytes(obj: object) -> bytes:
        """Serialize Python object to JSON bytes (zero-copy for WebSocket)."""
        return orjson.dumps(obj, option=orjson.OPT_NON_STR_KEYS)

    _BACKEND = "orjson"

except ImportError:
    import json as _json

    def loads(data: str | bytes) -> dict | list:  # type: ignore[misc]
        """Parse JSON string to Python object (stdlib fallback)."""
        return _json.loads(data)

    def dumps(obj: object, *, indent: bool = False, default: object = None) -> str:  # type: ignore[misc]
        """Serialize Python object to JSON string (stdlib fallback)."""
        kw: dict = {"separators": (",", ":")}
        if indent:
            kw["indent"] = 2
            kw.pop("separators", None)
        if default is not None:
            kw["default"] = default
        return _json.dumps(obj, **kw)

    def dumps_bytes(obj: object) -> bytes:  # type: ignore[misc]
        """Serialize Python object to JSON bytes (stdlib fallback)."""
        return _json.dumps(obj, separators=(",", ":")).encode("utf-8")

    _BACKEND = "stdlib"
