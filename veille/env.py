"""Loading the local `.env` file.

The pipeline never reads secrets from a file it owns: the API key lives in the
environment. To keep that usable without any shell setup, a `.env` file at the project
root is loaded at startup. An existing environment variable is never overwritten, so an
explicit `export` in the shell always wins over the file.

The parser is deliberately small and predictable: `KEY=VALUE`, `#` comments, blank
lines, an optional `export ` prefix and optional surrounding quotes. Escape sequences
are not interpreted, so a value containing a backslash keeps it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from veille.store import ROOT

ENV_PATH = ROOT / ".env"

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _unquote(value: str) -> str:
    """Remove one pair of matching surrounding quotes, if present."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse the content of a `.env` file.

    A line that is not a valid assignment is ignored rather than fatal: a malformed
    `.env` must not stop the pipeline.

    Args:
        text: Content of the file.

    Returns:
        The variables found, in file order.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()

        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _KEY_PATTERN.match(key):
            continue
        values[key] = _unquote(value.strip())
    return values


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Load a `.env` file into the process environment.

    Variables already present in the environment are left untouched.

    Args:
        path: File to read; defaults to `.env` at the project root.

    Returns:
        The variables this call actually set, for reporting and for tests.
    """
    env_path = path if path is not None else ENV_PATH
    if not env_path.exists():
        return {}

    loaded: dict[str, str] = {}
    for key, value in parse_dotenv(env_path.read_text(encoding="utf-8")).items():
        if key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded
