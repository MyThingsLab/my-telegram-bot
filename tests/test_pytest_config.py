from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import mytelegrambot


def test_pyproject_declares_src_on_the_pytest_path() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    pythonpath = config["tool"]["pytest"]["ini_options"]["pythonpath"]
    assert pythonpath == ["src"], (
        "without pythonpath = ['src'], the suite imports whatever mytelegrambot install "
        "sits in the shared venv (main's checkout) instead of this worktree's source"
    )


def test_imported_package_resolves_to_this_checkouts_src() -> None:
    repo_src = Path(__file__).resolve().parents[1] / "src" / "mytelegrambot"
    imported = Path(mytelegrambot.__file__).resolve().parent
    assert imported == repo_src, (
        f"mytelegrambot imported from {imported}, not this checkout's {repo_src} — "
        "the suite is exercising a different checkout's code"
    )


def test_src_is_first_on_sys_path_ahead_of_any_editable_install() -> None:
    repo_src = str(Path(__file__).resolve().parents[1] / "src")
    assert repo_src in sys.path
