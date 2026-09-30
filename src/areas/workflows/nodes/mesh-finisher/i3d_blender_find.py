"""Find a Blender 4.2+ executable for the Mesh Finisher node.

Adapted from image-to-3dlab's image_to_3dlab/blender.py (Apache-2.0, Bingeljell) for
Modly. Search order:

1. ``MODLY_BLENDER`` — an explicit override always wins.
2. A portable copy under the app folder: each ancestor of this file is checked for a
   ``.tools/blender*/blender.exe`` (that is where this port unpacks Blender).
3. ``blender`` on the PATH.
4. The usual Windows install roots (``Blender Foundation\\Blender*``), newest first.

Nothing here installs Blender: it is a ~350 MB application the user chooses to install.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

ENV_VAR = "MODLY_BLENDER"
MIN_VERSION = (4, 2)
DOWNLOAD_URL = "https://www.blender.org/download/"


def _comparable(version: tuple[int, int] | None) -> tuple[int, int]:
    return version if version else (999, 999)


def candidates() -> list[Path]:
    """Usual locations for a Blender executable, most likely first."""
    found: list[Path] = []

    # Portable copies under any ancestor's .tools folder (this port's own install).
    try:
        here = Path(__file__).resolve()
        for parent in list(here.parents)[:6]:
            tools = parent / ".tools"
            if tools.is_dir():
                found += [p for p in sorted(tools.glob("blender*/blender.exe"), reverse=True) if p.is_file()]
                if found:
                    break
    except OSError:
        pass

    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            root = Path(base) / "Blender Foundation"
            if root.is_dir():
                found += [p for p in sorted(root.glob("Blender*/blender.exe"), reverse=True) if p.is_file()]

    # Cross-platform completeness: the paths the upstream finder knows.
    found.append(Path("/Applications/Blender.app/Contents/MacOS/Blender"))
    found += [p for p in sorted(Path("/opt").glob("blender*/blender"), reverse=True) if p.is_file()]

    return found


def find_blender() -> Path | None:
    """The Blender executable to use, or None when there is none."""
    configured = os.environ.get(ENV_VAR)
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    on_path = shutil.which("blender")
    if on_path:
        return Path(on_path)
    return next((p for p in candidates() if p.is_file()), None)


def parse_version(text: str) -> tuple[int, int] | None:
    """``Blender 4.5.14 LTS`` -> (4, 5)."""
    match = re.search(r"Blender\s+(\d+)\.(\d+)", text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def blender_version(path: Path) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_version(result.stdout or "")


def version_problem(version: tuple[int, int] | None) -> str | None:
    """Why this Blender may not work, or None when it is new enough (or unreadable)."""
    if version is not None and version < MIN_VERSION:
        return (
            f"Blender {version[0]}.{version[1]} is older than "
            f"{MIN_VERSION[0]}.{MIN_VERSION[1]}; the finish stages may fail. "
            f"Get a newer one from {DOWNLOAD_URL}"
        )
    return None


def missing_help() -> str:
    """What to tell someone who has no Blender."""
    return (
        f"Finish needs Blender {MIN_VERSION[0]}.{MIN_VERSION[1]} or newer for its "
        f"retopology and bake stages, and none was found. Install it from {DOWNLOAD_URL} "
        f"(the portable .zip works: unpack it so that `<folder>/.tools/blender-*/"
        f"blender.exe` exists, or set {ENV_VAR} to blender.exe)."
    )
