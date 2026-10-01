"""
Mesh Remesher — built-in process extension.

Protocol: reads one JSON line from stdin, writes JSON lines to stdout.
  stdin : { input, params, workspaceDir, tempDir }
  stdout: { type: "progress"|"log"|"done"|"error", ... }

Hole-safe remeshing pipeline for dirty AI-generated meshes:

  1. pre-clean    weld seam cracks, strip duplicate / degenerate / folded faces,
                  repair non-manifold edges and vertices, close small
                  pre-existing holes. Remeshing a dirty mesh is what tears it
                  open, so this stage is what keeps the output watertight.
  2. remesh       isotropic explicit remeshing, feature-preserving, with a
                  surface-distance check so vertices never drift off the
                  original skin.
  3. hole guard   remeshing and decimation both open pinholes wherever the
                  input was still non-manifold; after every destructive stage
                  the mesh is repaired and small holes are closed again.
  4. decimate     quadric edge collapse with topology and boundary
                  preservation when a target face count is set, followed by a
                  final hole-guard pass.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

try:
    import pymeshlab
except ImportError:
    pymeshlab = None


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def progress(pct: int, label: str) -> None:
    emit({"type": "progress", "percent": pct, "label": label})


def log(msg: str) -> None:
    emit({"type": "log", "message": msg})


def done(file_path: str) -> None:
    emit({"type": "done", "result": {"filePath": file_path}})


def error(msg: str) -> None:
    emit({"type": "error", "message": msg})


def topo(ms) -> dict:
    return ms.get_topological_measures()


def topology_log(ms, label: str) -> dict:
    m = topo(ms)
    holes       = m["number_holes"]
    holes_text  = f"holes {holes}, " if holes >= 0 else ""
    log(f"{label}: {m['vertices_number']} verts, {m['faces_number']} faces, "
        f"{holes_text}boundary edges {m['boundary_edges']}, "
        f"non-manifold edges {m['non_two_manifold_edges']}")
    return m


def repair_non_manifold(ms) -> None:
    # method=0 removes offending faces (low memory); method=1 detaches (OOMs on dense meshes)
    try:
        ms.meshing_repair_non_manifold_edges(method=0)
    except Exception as exc:
        log(f"Non-manifold edge repair skipped: {exc}")
    try:
        ms.meshing_repair_non_manifold_vertices()
    except Exception as exc:
        log(f"Non-manifold vertex repair skipped: {exc}")
    try:
        ms.meshing_remove_null_faces()
    except Exception as exc:
        log(f"Null face removal skipped: {exc}")


def close_holes_safe(ms, max_hole_size: int, label: str, passes: int = 3) -> None:
    """Close small holes, repeating while the open boundary keeps shrinking.

    One pass of meshing_close_holes can leave holes open when the fill itself
    is not manifold (common on meshes with near-touching walls), so it is
    retried while it keeps making progress. Boundary edge count is the honest
    signal here: every open hole has at least one, while number_holes reads -1
    (unknown) until some filter has happened to compute the topology.
    """
    closed = 0
    for _ in range(passes):
        m = topo(ms)
        if m["boundary_edges"] == 0:
            break
        holes_before    = m["number_holes"]
        boundary_before = m["boundary_edges"]
        try:
            ms.meshing_close_holes(
                maxholesize=max_hole_size,
                newfaceselected=False,
                selfintersection=False,
            )
        except Exception as exc:
            log(f"{label}: hole fill skipped (mesh may still be non-manifold): {exc}")
            break
        m2 = topo(ms)
        if holes_before >= 0 and m2["number_holes"] >= 0:
            closed += max(0, holes_before - m2["number_holes"])
        if m2["boundary_edges"] >= boundary_before:
            break
    if closed:
        log(f"{label}: closed {closed} hole(s) (max {max_hole_size} edges)")
    remaining = topo(ms)["boundary_edges"]
    if remaining > 0:
        log(f"{label}: {remaining} boundary edge(s) still open — holes larger "
            f"than {max_hole_size} edges are left alone")


def pre_clean(ms, weld_distance: float, fill_holes: bool, max_hole_size: int) -> None:
    """Make the mesh remeshable without tearing: the whole point of this stage.

    AI exports arrive split along every UV seam and sprinkled with non-manifold
    edges. The isotropic remesher walks those seams and tears pinholes at every
    unwelded pair of vertices and every non-manifold fan it meets.
    """
    if weld_distance > 0:
        ms.meshing_merge_close_vertices(threshold=pymeshlab.PureValue(weld_distance))
    ms.meshing_remove_duplicate_vertices()
    ms.meshing_remove_duplicate_faces()
    ms.meshing_remove_null_faces()
    try:
        ms.meshing_remove_folded_faces()
    except Exception as exc:
        log(f"Folded face removal skipped: {exc}")
    repair_non_manifold(ms)
    try:
        ms.meshing_remove_unreferenced_vertices()
    except Exception as exc:
        log(f"Unreferenced vertex removal skipped: {exc}")
    if fill_holes:
        close_holes_safe(ms, max_hole_size, "Pre-clean")


def remesh(ms, edge_length: float, iterations: int, preserve_features: bool) -> None:
    ms.meshing_isotropic_explicit_remeshing(
        targetlen=pymeshlab.PureValue(edge_length),
        iterations=iterations,
        adaptive=False,
        featuredeg=30.0 if preserve_features else 180.0,
        checksurfdist=True,
        # Vertices may wander at most half an edge length off the original
        # surface; this keeps smoothing from flattening small detail.
        maxsurfdist=pymeshlab.PureValue(edge_length * 0.5),
        splitflag=True,
        collapseflag=True,
        swapflag=True,
        smoothflag=True,
        reprojectflag=True,
    )


def quad_dominant(ms) -> str:
    """Pair triangles into quads, then re-triangulate along the quads' best diagonals.

    The output GLB is triangulated either way; the pass is worth it because the
    diagonal chosen inside each quad follows the surface flow better than the
    raw remesh triangles. Returns a note for the log.
    """
    try:
        ms.meshing_tri_to_quad_dominant(level="Fewest triangles")
        ms.meshing_poly_to_tri()
        return "quad-dominant pairing applied"
    except Exception as exc:
        return f"quad pairing unavailable, kept triangles ({exc})"


def decimate(ms, target_count: int) -> None:
    # preservetopology refuses collapses that would fold the surface into
    # non-manifold configurations — those read as holes in a viewer — and
    # preserveboundary keeps existing boundary loops from being eaten.
    current_faces = int(ms.current_mesh().face_number())
    try:
        ms.meshing_decimation_quadric_edge_collapse(
            targetfacenum=int(target_count),
            qualitythr=0.3,
            preserveboundary=True,
            preservetopology=True,
            optimalplacement=True,
            autoclean=True,
        )
    except Exception:
        ms.meshing_decimation_quadric_edge_collapse(targetfacenum=int(target_count))
    log(f"Decimated {current_faces} -> {int(ms.current_mesh().face_number())} faces")


def main() -> None:
    raw   = sys.stdin.readline()
    data  = json.loads(raw)

    input_data    = data.get("input", {})
    params        = data.get("params", {})
    workspace_dir = data.get("workspaceDir", "")

    input_path = input_data.get("filePath")
    if not input_path or not Path(input_path).is_file():
        error(f"mesh-remesher: input file not found: {input_path}")
        return

    mode               = str(params.get("mode", "triangle"))
    target_edge_length = float(params.get("target_edge_length", 0.0) or 0.0)
    target_count = int(float(params.get("target_count", 0) or 0))
    quality            = str(params.get("quality", "fine")).strip().lower()
    preserve_features  = bool(params.get("preserve_features", True))
    weld_distance      = float(params.get("weld_distance", 0.0) or 0.0)
    fill_holes         = bool(params.get("fill_holes", True))
    max_hole_size      = int(params.get("max_hole_size", 500))

    iterations = {"draft": 3, "standard": 6}.get(quality, 10)

    out_dir = Path(workspace_dir) / "Workflows"
    out_dir.mkdir(parents=True, exist_ok=True)
    from time import time
    out_path = str(out_dir / f"mesh-remesher-{int(time() * 1000)}.glb")

    log(f"Mode: {mode}, edge length: {target_edge_length or 'auto'}, "
        f"target count: {target_count or 'off'}, quality: {quality} "
        f"({iterations} iterations)")

    if mode == "none":
        progress(50, "Passing through…")
        shutil.copy2(input_path, out_path)
        progress(100, "Done")
        done(out_path)
        return

    if pymeshlab is None:
        error("mesh-remesher: pymeshlab is not available on this system")
        return

    import numpy as np
    import trimesh

    progress(10, "Loading mesh…")
    loaded = trimesh.load(input_path)
    if isinstance(loaded, trimesh.Scene):
        geoms = list(loaded.geometry.values())
        geom  = trimesh.util.concatenate(geoms) if len(geoms) > 1 else geoms[0]
    else:
        geom = loaded

    bbox_diagonal = float(np.linalg.norm(geom.extents)) if len(geom.vertices) else 0.0
    if weld_distance <= 0 and bbox_diagonal > 0:
        # 0.1% of the diagonal: enough to weld the hairline cracks a GLB export
        # leaves along UV seams, far below the scale of real thin features.
        weld_distance = bbox_diagonal * 0.001

    tmp_dir = tempfile.mkdtemp()
    try:
        ply_in  = os.path.join(tmp_dir, "input.ply")
        ply_out = os.path.join(tmp_dir, "output.ply")
        geom.export(ply_in)

        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(ply_in)

        progress(25, "Cleaning topology…")
        topology_log(ms, "Input")
        pre_clean(ms, weld_distance, fill_holes, max_hole_size)
        topology_log(ms, "Pre-cleaned")

        measures = ms.get_geometric_measures()
        surface_area = float(measures.get("surface_area", 0.0))
        faces_now = topo(ms)["faces_number"]
        if target_count > 0:
            if surface_area > 0:
                # Equilateral-triangle area model; the 1.15 bias compensates the
                # remesher's tendency to land just under the requested count.
                target_edge_length = (surface_area / (0.4325 * target_count * 1.15)) ** 0.5
            else:
                target_edge_length = float(measures.get("avg_edge_length", 0.02))
            log(f"Target count {target_count}: area {surface_area:.6f} "
                f"-> edge length {target_edge_length:.6f}")
        elif target_edge_length <= 0:
            # Keep the current sampling density: derive the edge length from the
            # cleaned face count and surface area instead of avg_edge_length,
            # which a handful of huge faces can throw far off.
            if surface_area > 0 and faces_now > 0:
                target_edge_length = (surface_area / (0.4325 * faces_now)) ** 0.5
            else:
                target_edge_length = float(measures.get("avg_edge_length", 0.02))
            log(f"Auto edge length: {target_edge_length:.6f}")

        progress(45, f"Remeshing ({mode})…")
        remesh(ms, target_edge_length, iterations, preserve_features)
        if mode == "quad":
            log(f"Quad mode: {quad_dominant(ms)}")
        topology_log(ms, "Remeshed")

        progress(65, "Repairing and closing holes…")
        repair_non_manifold(ms)
        if fill_holes:
            close_holes_safe(ms, max_hole_size, "Post-remesh")

        if target_count > 0 and mode != "none":
            current_faces = int(ms.current_mesh().face_number())
            if current_faces > target_count:
                progress(75, "Decimating…")
                decimate(ms, target_count)
                repair_non_manifold(ms)
                if fill_holes:
                    close_holes_safe(ms, max_hole_size, "Post-decimate")

        topology_log(ms, "Final")
        if topo(ms)["boundary_edges"] > 0:
            if fill_holes:
                log(f"Warning: open boundary remains — holes larger than Max Hole "
                    f"Size ({max_hole_size} edges) are not filled. Raise Max Hole "
                    "Size or run Mesh Repair.")
            else:
                log("Fill Holes is off: open boundaries were left open.")

        progress(90, "Exporting…")
        ms.save_current_mesh(ply_out)
        _loaded = trimesh.load(ply_out, process=False)
        if isinstance(_loaded, trimesh.Scene):
            _geoms = list(_loaded.geometry.values())
            _loaded = _geoms[0] if len(_geoms) == 1 else trimesh.util.concatenate(_geoms)
        # Rebuild from raw vertices/faces: interpreting a PLY's stored colours
        # can require scipy, which the runtime venv does not ship, and remeshing
        # makes any surviving colour data meaningless anyway.
        result = trimesh.Trimesh(vertices=_loaded.vertices, faces=_loaded.faces,
                                 process=False)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    result.export(out_path)
    log(f"Output: {out_path} ({len(result.faces)} faces, "
        f"watertight: {bool(result.is_watertight)})")
    progress(100, "Done")
    done(out_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        error(f"{exc}\n{traceback.format_exc()}")
