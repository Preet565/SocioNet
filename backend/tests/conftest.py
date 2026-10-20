"""Keep import-time API initialization away from the user's live database.

The API constructs its default service during module import. Set an isolated
database before collection imports any test module, not in a fixture that runs
after collection. Individual tests still use their own tmp_path databases.
"""

from tempfile import TemporaryDirectory
from pathlib import Path

import pytest


_directory: TemporaryDirectory | None = None
_environment: pytest.MonkeyPatch | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _directory, _environment
    _directory = TemporaryDirectory(prefix="socionet-pytest-")
    _environment = pytest.MonkeyPatch()
    _environment.setenv("SOCIONET_DB_PATH", str(Path(_directory.name) / "collection.sqlite3"))
    _environment.setenv("SOCIONET_SERVE_FRONTEND", "0")


def pytest_unconfigure(config: pytest.Config) -> None:
    if _environment is not None:
        _environment.undo()
    if _directory is not None:
        _directory.cleanup()
