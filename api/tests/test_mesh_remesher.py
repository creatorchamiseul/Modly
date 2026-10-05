"""Regression tests for the mesh-remesher processor's hole-safe pipelines.

The remesher used to tear holes into AI-generated meshes because it remeshed
dirty topology directly. Two guards are tested:

- the SDF route (default): a self-contained voxel remesher whose marching-
  tetrahedra extraction is watertight by construction, exercised on a mesh
  carrying the classic pathologies (boundary holes, UV-seam cracks, a
  non-manifold edge, degenerate faces);
- the triangle route: pymeshlab isotropic remeshing with pre-cleaning and a
  hole guard.

They need pymeshlab + trimesh (the API runtime venv has both); they skip
cleanly where those are missing.
"""
from __future__ import annotations

import json
import os
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
    # Pin the numpy engine: these tests assert the built-in remesher's exact
    # behaviour, and with Blender installed the SDF route would go through the
    # voxel quad remesher instead (quad counts read as doubled in the GLB).
    proc = subprocess.run(
        [sys.executable, str(PROCESSOR)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        timeout=280,
        env={**os.environ, "MODLY_REMESH_ENGINE": "numpy"},
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

    def _remesh(self, mesh, params):
        result = _run_processor(mesh, params, self.workspace)
        self.assertEqual(result["errors"], [])
        self.assertIsNotNone(result["output"], "processor did not report done")
        return trimesh.load(result["output"], force="mesh")

    # ---- SDF route (Modly's own, default) ------------------------------------

    def test_sdf_dirty_mesh_comes_out_watertight(self):
        out = self._remesh(_dirty_mesh(), {"mode": "sdf", "sdf_resolution": 48})
        self.assertTrue(out.is_watertight,
                        "SDF remesh output has holes; extraction is not closed")
        self.assertTrue(out.is_winding_consistent,
                        "inward-facing triangles read as holes when backfaces are culled")

    def test_sdf_preserves_genus(self):
        torus = trimesh.creation.torus(major_radius=0.7, minor_radius=0.25)
        out = self._remesh(torus, {"mode": "sdf", "sdf_resolution": 48})
        self.assertTrue(out.is_watertight)
        self.assertEqual(out.euler_number, 0,
                         "a torus must stay genus 1 through the SDF remesh")

    def test_sdf_keeps_separate_components(self):
        two = trimesh.util.concatenate([
            trimesh.creation.icosphere(subdivisions=3),
            trimesh.creation.icosphere(subdivisions=3).apply_translation([2.5, 0, 0]),
        ])
        out = self._remesh(two, {"mode": "sdf", "sdf_resolution": 48})
        self.assertTrue(out.is_watertight)
        self.assertEqual(out.euler_number, 4, "two closed spheres must survive as two")

    def test_sdf_respects_target_count(self):
        sphere = trimesh.creation.icosphere(subdivisions=4)
        out = self._remesh(sphere, {"mode": "sdf", "target_count": 8000})
        self.assertTrue(out.is_watertight)
        self.assertLess(len(out.faces), 8000 * 1.35,
                        "SDF output should land near the requested face count")

    # ---- triangle route -------------------------------------------------------

    def test_triangle_dirty_mesh_comes_out_watertight(self):
        out = self._remesh(_dirty_mesh(), {"mode": "triangle", "target_count": 2000})
        self.assertTrue(out.is_watertight,
                        "remesh output has holes; pipeline failed to close them")
        self.assertTrue(out.is_winding_consistent)

    def test_triangle_quad_mode_comes_out_watertight(self):
        out = self._remesh(_dirty_mesh(), {"mode": "quad", "target_count": 2000})
        self.assertTrue(out.is_watertight)

    def test_triangle_fill_holes_off_respects_open_boundary(self):
        mesh = trimesh.creation.icosphere(subdivisions=3)
        mesh.faces = mesh.faces[:900]
        out = self._remesh(mesh, {"mode": "triangle", "target_count": 2000,
                                  "fill_holes": False})
        self.assertFalse(out.is_watertight,
                         "fill_holes=False must leave the open boundary open")


if __name__ == "__main__":
    unittest.main()
