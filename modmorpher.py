from __future__ import annotations

from pathlib import Path

tool_version = "1.6.2"

_BASE = Path(__file__).resolve().parent
_PARTS = [
    "core.py",
    "assets.py",
    "conversion.py",
    "pipeline.py",
]

for _part in _PARTS:
    _path = _BASE / _part
    with _path.open("r", encoding="utf-8") as _fh:
        _code = compile(_fh.read(), str(_path), "exec")
        exec(_code, globals(), globals())
