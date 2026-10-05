"""Find a Blender 4.2+ executable for the mesh-remesher node.

Same search policy as the Finish node's finder (copied for this node so it
stays self-contained): MODLY_BLENDER wins, then a portable copy under any
ancestor's ``.tools`` folder, then ``blender`` on PATH, then the usual
Windows install roots. Nothing here installs Blender.
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


def candidates() -> list[Path]:
    found: list[Path] = []
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
    if version is not None and version < MIN_VERSION:
        return (f"Blender {version[0]}.{version[1]} is older than "
                f"{MIN_VERSION[0]}.{MIN_VERSION[1]}; quad remeshing may fail.")
    return None


def missing_help() -> str:
    return (f"Quad remeshing needs Blender {MIN_VERSION[0]}.{MIN_VERSION[1]} or newer, "
            f"and none was found. Install it from {DOWNLOAD_URL} (the portable .zip "
            f"unpacked under the app's `.tools` folder works), or set {ENV_VAR}.")
