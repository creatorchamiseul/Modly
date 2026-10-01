"""Regression tests for the mesh-remesher processor's hole-safe pipeline.

The remesher used to tear holes into AI-generated meshes because it remeshed
dirty topology directly. These tests build a mesh carrying the classic
pathologies (boundary holes, UV-seam cracks, a non-manifold edge, degenerate
faces) and assert the output comes out watertight.

They need pymeshlab + trimesh (the API runtime venv has both); they skip
cleanly where those are missing.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    import trimesh

    HAS_MESH_DEPS = True
except ImportError:
    HAS_MESH_DEPS = False

PROCESSOR = Path(__file__).resolve().parents[2] / "src" / "areas" / "workflows" / "nodes" / "mesh-remesher" / "processor.py"


def _dirty_mesh() -> trimesh.Trimesh:
    """A sphere with holes punched, seam cracks, one non-manifold face and
    degenerate faces — the standard state of an AI-generated export."""
    rng = np.random.default_rng(7)
    mesh = trimesh.creation.icosphere(subdivisions=3)
    V = mesh.vertices.copy()
    F = mesh.faces.copy()

    F = F[:900]  # punch a hole

    # seam cracks: duplicate a strip of vertices, nudge the copies apart
    on_seam = np.where(np.abs(V[:, 0]) < 1e-9)[0][:80]
    new_v = V[on_seam] + rng.normal(0, 2e-4, (len(on_seam), 3))
    remap = {int(o): len(V) + i for i, o in enumerate(on_seam)}
    V = np.vstack([V, new_v])
    F = np.array([[remap.get(int(a), a), remap.get(int(b), b), remap.get(int(c), c)]
                  for a, b, c in F])

    # non-manifold edge: a second face sharing an existing edge
    F = np.vstack([F, np.array([[F[0][0], F[0][1], len(V) - 1]])])
    # degenerate faces
    F = np.vstack([F, np.array([[0, 0, 1], [5, 5, 6]])])

    return trimesh.Trimesh(vertices=V, faces=F, process=False)


def _run_processor(mesh: trimesh.Trimesh, params: dict, workspace: Path) -> dict:
    mesh_path = workspace / "input.glb"
    mesh.export(mesh_path)
    payload = {
        "input": {"filePath": str(mesh_path)},
        "params": params,
        "workspaceDir": str(workspace),
        "tempDir": str(workspace),
    }
    proc = subprocess.run(
        [sys.executable, str(PROCESSOR)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=280,
    )
    events = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if line.startswith("{"):
            events.append(json.loads(line))
    errors = [e["message"] for e in events if e.get("type") == "error"]
    done = [e for e in events if e.get("type") == "done"]
    return {"errors": errors, "output": done[0]["result"]["filePath"] if done else None}


@unittest.skipUnless(HAS_MESH_DEPS, "trimesh/numpy not available")
@unittest.skipUnless(PROCESSOR.is_file(), "mesh-remesher processor not found")
class TestRemesherHoleSafety(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_dirty_mesh_comes_out_watertight(self):
        result = _run_processor(_dirty_mesh(), {"mode": "triangle", "target_count": 2000},
                                self.workspace)
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["output"], "processor did not report done")
        out = trimesh.load(result["output"], force="mesh")
        self.assertTrue(out.is_watertight,
                        "remesh output has holes; pipeline failed to close them")
        self.assertTrue(out.is_winding_consistent)

    def test_quad_mode_comes_out_watertight(self):
        result = _run_processor(_dirty_mesh(), {"mode": "quad", "target_count": 2000},
                                self.workspace)
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["output"])
        out = trimesh.load(result["output"], force="mesh")
        self.assertTrue(out.is_watertight)

    def test_fill_holes_off_respects_open_boundary(self):
        mesh = trimesh.creation.icosphere(subdivisions=3)
        mesh.faces = mesh.faces[:900]
        result = _run_processor(mesh, {"mode": "triangle", "target_count": 2000,
                                       "fill_holes": False}, self.workspace)
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["output"])
        out = trimesh.load(result["output"], force="mesh")
        self.assertFalse(out.is_watertight,
                        "fill_holes=False must leave the open boundary open")


if __name__ == "__main__":
    unittest.main()
