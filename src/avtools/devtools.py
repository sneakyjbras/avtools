"""Developer-only command wrappers for local repository workflows."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT_RELATIVE_PATH = Path("scripts") / "run-checks.sh"


def _find_project_script() -> Path:
    """Find the repository-local pre-commit wrapper script.

    Returns:
        Absolute path to ``scripts/run-checks.sh``.

    Raises:
        FileNotFoundError: If the script cannot be located from the current
            working directory or any of its parents.
    """
    search_roots = [Path.cwd(), *Path.cwd().parents]

    package_root = Path(__file__).resolve()
    search_roots.extend(package_root.parents)

    for root in search_roots:
        script_path = root / SCRIPT_RELATIVE_PATH
        if script_path.is_file():
            return script_path

    raise FileNotFoundError(
        "Could not find scripts/run-checks.sh. "
        "Run this command from the av-tools repository root."
    )


def precommit_checks() -> None:
    """Run formatting and linting checks through the repository shell wrapper.

    Delegates to ``scripts/run-checks.sh``, which runs Black (check + apply)
    followed by Ruff, and prints a quality summary.

    Usage::

        poetry run precommit-checks
    """
    try:
        script_path = _find_project_script()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    sys.exit(subprocess.call([str(script_path)]))
