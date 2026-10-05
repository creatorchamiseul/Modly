"""
Mesh Remesher — built-in process extension.

Protocol: reads one JSON line from stdin, writes JSON lines to stdout.
  stdin : { input, params, workspaceDir, tempDir }
  stdout: { type: "progress"|"log"|"done"|"error", ... }

Remeshing routes:

  SDF (default)  when Blender is installed, the input is resampled into a
                 closed, all-quad skin with Blender's voxel remesher
                 (quad_remesh.py) and the source texture is baked onto it.
                 Without Blender it falls back to Modly's own numpy remesher
                 (sdf_remesh.py; triangle output, no texture transfer).
  Triangle       pymeshlab isotropic remeshing with a pre-clean, feature
                 preservation and a hole guard after every destructive pass.
  Quad-dominant  the triangle route plus a tri-to-quad pairing pass.

The pymeshlab routes never carry UVs, so when Blender is available and
transfer_texture is on, their output also goes through
blender_texture_transfer.py to bake the source texture onto the new surface.
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    import pymeshlab
except ImportError:
    pymeshlab = None

import sdf_remesh

try:
    import blender_find
except Exception:  # the finder is optional; without it only numpy routes run
    blender_find = None


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def progress(pct: int, label: str) -> None:
    emit({"type": "progress", "percent": pct, "label": label})


def log(msg: str) -> None:
    emit({"type": "log", "message": str(msg)[:2000]})


def done(file_path: str) -> None:
    emit({"type": "done", "result": {"filePath": file_path}})


def error(msg: str) -> None:
    emit({"type": "error", "message": msg})


def _flag(params: dict, key: str, default: bool) -> bool:
    value = params.get(key, "yes" if default else "no")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"yes", "on", "true", "1"}


def _has_base_colour_texture(path: str) -> bool:
    """Whether a GLB carries a base-colour texture (cheap JSON-chunk peek)."""
    try:
        data = open(path, "rb").read()
        if data[:4] != b"glTF":
            return False
        length = struct.unpack_from("<I", data, 12)[0]
        doc = json.loads(data[20:20 + length])
        for material in doc.get("materials", []):
            if (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture"):
                return True
    except Exception:
        return False
    return False


def _blender_env(blender: Path) -> dict:
    """Environment for Blender, keeping user files in the app folder tree.

    Same policy as the Finish node: when the executable lives under a
    ``.tools`` directory, config/scripts/temp point back into that tree so a
    headless run adds nothing to the user's home drive.
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    try:
        parts = blender.resolve().parts
        for index in range(len(parts) - 1, -1, -1):
            if parts[index] == ".tools":
                root = Path(*parts[:index])
                user = root / ".tools" / "blender-data"
                env["BLENDER_USER_CONFIG"] = str(user / "config")
                env["BLENDER_USER_SCRIPTS"] = str(user / "scripts")
                env["BLENDER_USER_DATAFILES"] = str(user / "datafiles")
                temp = root / ".cache" / "blender-tmp"
                temp.mkdir(parents=True, exist_ok=True)
                env["TEMP"] = str(temp)
                env["TMP"] = str(temp)
                break
    except OSError:
        pass
    return env


def _run_blender(blender: Path, script: Path, args: list, out_dir: Path,
                 label: str) -> None:
    """Run one Blender stage, teeing output to a log, relaying marker lines."""
    command = [str(blender), "--background", "--factory-startup",
               "--python", str(script), "--", *[str(a) for a in args]]
    log(f"Blender: {script.name} ...")
    log_path = out_dir / f"mesh-remesher-{int(time.time() * 1000)}-{label}.log"
    env = _blender_env(blender)
    tail: list[str] = []
    with log_path.open("w", encoding="utf-8", errors="replace") as handle:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
            cwd=str(Path(script).parent),
        )
        assert process.stdout is not None
        for line in process.stdout:
            handle.write(line)
            stripped = line.rstrip()
            if stripped:
                tail.append(stripped)
                del tail[:-30]
                if stripped.startswith(("QUADRS::", "TEXFER::")):
                    log(stripped[:500])
        code = process.wait()
    if code != 0:
        raise RuntimeError(f"{script.name} failed (exit {code}); last lines:\n"
                           + "\n".join(tail[-12:]))


def topo(ms) -> dict:
    return ms.get_topological_measures()


def topology_log(ms, label: str) -> dict:
    m = topo(ms)
    holes      = m["number_holes"]
    holes_text = f"holes {holes}, " if holes >= 0 else ""
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
    edges. The remeshers walk those seams and tear pinholes at every unwelded
    pair of vertices and every non-manifold fan they meet — and the SDF route
    needs a closed mesh for its parity sign, so holes are closed here.
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
        ms.meshing_re_orient_faces_coherently()
    except Exception as exc:
        log(f"Coherent re-orientation skipped: {exc}")
    try:
        ms.meshing_remove_unreferenced_vertices()
    except Exception as exc:
        log(f"Unreferenced vertex removal skipped: {exc}")
    if fill_holes:
        close_holes_safe(ms, max_hole_size, "Pre-clean")


def remesh_isotropic(ms, edge_length: float, iterations: int, preserve_features: bool) -> None:
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


def sdf_resolution_for(target_count: int, surface_area: float, bbox_diagonal: float,
                       current: int = 128) -> int:
    """Grid resolution that lands near `target_count` faces.

    Measured on a sphere: faces ≈ 9 · (area/diag²) · (resolution − 4)². The
    retry loop after extraction corrects the inevitable shape-to-shape drift.
    """
    if target_count <= 0 or surface_area <= 0 or bbox_diagonal <= 0:
        return current
    area_norm = surface_area / (bbox_diagonal * bbox_diagonal)
    n = int(round((target_count / (9.0 * area_norm)) ** 0.5)) + 4
    return int(max(16, min(256, n)))


def _strip_non_finite(geom):
    """Drop NaN/Inf vertices and every face touching one, from a trimesh."""
    import numpy as np
    import trimesh

    verts = np.asarray(geom.vertices, dtype=np.float64)
    finite = np.isfinite(verts).all(axis=1)
    if finite.all():
        return geom
    faces = np.asarray(geom.faces, dtype=np.int64)
    bad_faces = ~finite[faces].all(axis=1)
    keep = np.flatnonzero(finite)
    remap = np.full(len(verts), -1, dtype=np.int64)
    remap[keep] = np.arange(len(keep), dtype=np.int64)
    cleaned = trimesh.Trimesh(vertices=verts[keep],
                              faces=remap[faces[~bad_faces]], process=False)
    log(f"Removed {int((~finite).sum())} non-finite vertices and "
        f"{int(bad_faces.sum())} faces touching them")
    return cleaned


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

    mode               = str(params.get("mode", "sdf")).strip().lower()
    target_edge_length = float(params.get("target_edge_length", 0.0) or 0.0)
    target_count = int(float(params.get("target_count", 50000) or 0))
    quality            = str(params.get("quality", "fine")).strip().lower()
    preserve_features  = _flag(params, "preserve_features", True)
    weld_distance      = float(params.get("weld_distance", 0.0) or 0.0)
    fill_holes         = _flag(params, "fill_holes", True)
    max_hole_size      = int(params.get("max_hole_size", 500))
    sdf_resolution     = int(float(params.get("sdf_resolution", 0) or 0))
    smoothing          = float(params.get("smoothing", 0.6) or 0.0)
    smoothing = max(0.0, min(1.0, smoothing))
    transfer_texture   = _flag(params, "transfer_texture", True)

    iterations = {"draft": 3, "standard": 6}.get(quality, 10)

    out_dir = Path(workspace_dir) / "Workflows"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    out_path = str(out_dir / f"mesh-remesher-{stamp}.glb")

    log(f"Mode: {mode}, edge length: {target_edge_length or 'auto'}, "
        f"target count: {target_count or 'off'}, quality: {quality}, "
        f"texture transfer: {'on' if transfer_texture else 'off'}")

    if mode == "none":
        progress(50, "Passing through…")
        shutil.copy2(input_path, out_path)
        progress(100, "Done")
        done(out_path)
        return

    blender = None
    if blender_find is not None and os.environ.get("MODLY_REMESH_ENGINE", "auto").lower() != "numpy":
        blender = blender_find.find_blender()
        if blender is not None:
            log(f"Blender: {blender}")

    # ---- SDF route: Blender quad remesh first (best quality) -----------------
    # The numpy remesher below is a fallback for machines without Blender: its
    # flood-fill sign field shreds open shard-soup inputs, and it cannot make
    # quads or transfer textures. Blender's voxel remesher handles exactly
    # those inputs and produces an all-quad skin by construction.
    if mode == "sdf" and blender is not None:
        progress(15, "Quad remesh (Blender voxel)…")
        obj_path = str(out_dir / f"mesh-remesher-{stamp}_quads.obj")
        try:
            _run_blender(blender, HERE / "quad_remesh.py",
                         [input_path, out_path, obj_path, target_count,
                          "1" if transfer_texture else "0", 0.0],
                         out_dir, "quad-remesh")
            if Path(out_path).is_file() and Path(out_path).stat().st_size > 0:
                log(f"Output: {out_path}")
                progress(100, "Done (quad skin)")
                done(out_path)
                return
            log("Blender quad route produced no file; falling back to numpy SDF")
        except Exception as exc:
            log(f"Blender quad route failed: {exc}")
            log("Falling back to the built-in numpy SDF remesher")

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

    # Non-finite vertices poison every filter downstream and render as
    # nothing at all in a viewer ("the remesher ate my model"). Generator
    # exports can ship them; drop the vertices and every face touching one
    # before anything else sees the mesh.
    geom = _strip_non_finite(geom)

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
        # SDF mode always fills every hole: its parity sign needs a closed
        # mesh, and closing holes is what this mode is for in the first place.
        clean_fill_size = max(max_hole_size, 20000) if mode == "sdf" else max_hole_size
        pre_clean(ms, weld_distance, fill_holes or mode == "sdf", clean_fill_size)
        topology_log(ms, "Pre-cleaned")

        measures = ms.get_geometric_measures()
        surface_area = float(measures.get("surface_area", 0.0))

        if target_count > 0:
            if surface_area > 0:
                # Equilateral-triangle area model; the 1.15 bias compensates the
                # remesher's tendency to land just under the requested count.
                target_edge_length = (surface_area / (0.4325 * target_count * 1.15)) ** 0.5
            else:
                target_edge_length = float(measures.get("avg_edge_length", 0.02))
            log(f"Target count {target_count}: area {surface_area:.6f} "
                f"-> edge length {target_edge_length:.6f}")
        elif target_edge_length <= 0 and mode != "sdf":
            # Keep the current sampling density: derive the edge length from the
            # cleaned face count and surface area instead of avg_edge_length,
            # which a handful of huge faces can throw far off.
            faces_now = topo(ms)["faces_number"]
            if surface_area > 0 and faces_now > 0:
                target_edge_length = (surface_area / (0.4325 * faces_now)) ** 0.5
            else:
                target_edge_length = float(measures.get("avg_edge_length", 0.02))
            log(f"Auto edge length: {target_edge_length:.6f}")

        # ---------------- SDF route (numpy fallback) -------------------------
        if mode == "sdf":
            log("Using the built-in numpy SDF remesher (triangle output; "
                "texture transfer needs Blender)")
            resolution = sdf_resolution if sdf_resolution > 0 else \
                sdf_resolution_for(target_count, surface_area, bbox_diagonal)
            log(f"SDF remesh at grid resolution {resolution} "
                f"(smoothing {smoothing:.2f})")
            mesh = ms.current_mesh()
            verts_in = np.asarray(mesh.vertex_matrix(), dtype=np.float64)
            faces_in = np.asarray(mesh.face_matrix(), dtype=np.int64)

            progress(45, "SDF remeshing…")
            closed = topo(ms)["boundary_edges"] == 0
            if not closed:
                log("Mesh still has open cracks after cleaning; signing the "
                    "grid by flood fill (fragment-shell mode)")
            try:
                verts_out, faces_out, stats = sdf_remesh.remesh(
                    verts_in, faces_in, resolution, smoothing=smoothing,
                    closed=closed)
            except Exception as exc:
                log(f"SDF remesh failed ({exc}); falling back to the "
                    "triangle remesher")
                mode = "triangle"

            if mode == "sdf":
                # retry once at a higher resolution when the extraction badly
                # undershot the requested face count
                if (target_count > 0 and sdf_resolution <= 0
                        and len(faces_out) < 0.7 * target_count and resolution < 256):
                    resolution = int(min(256, round(resolution * 1.4)))
                    log(f"Extraction undershot ({len(faces_out)} faces); "
                        f"retrying at resolution {resolution}")
                    try:
                        verts_out, faces_out, stats = sdf_remesh.remesh(
                            verts_in, faces_in, resolution, smoothing=smoothing,
                            closed=closed)
                    except Exception as exc:
                        log(f"SDF retry failed ({exc}); keeping the first result")

                log(f"SDF stats: {stats}")
                sdf_mesh = pymeshlab.Mesh(vertex_matrix=verts_out,
                                          face_matrix=faces_out)
                ms.add_mesh(sdf_mesh, "sdf-remeshed")
                topology_log(ms, "SDF extracted")
                # sharp features can weld a few slivers into pinholes; close them
                repair_non_manifold(ms)
                close_holes_safe(ms, max_hole_size, "Post-SDF")

                if target_count > 0 and topo(ms)["faces_number"] > target_count:
                    progress(75, "Decimating…")
                    decimate(ms, target_count)
                    repair_non_manifold(ms)
                    close_holes_safe(ms, max_hole_size, "Post-decimate")

        # ---------------- pymeshlab routes ------------------------------------
        if mode in ("triangle", "quad"):
            progress(45, f"Remeshing ({mode})…")
            remesh_isotropic(ms, target_edge_length, iterations, preserve_features)
            if mode == "quad":
                log(f"Quad mode: {quad_dominant(ms)}")
            topology_log(ms, "Remeshed")

            progress(65, "Repairing and closing holes…")
            repair_non_manifold(ms)
            if fill_holes:
                close_holes_safe(ms, max_hole_size, "Post-remesh")

            if target_count > 0:
                current_faces = int(ms.current_mesh().face_number())
                if current_faces > target_count:
                    progress(75, "Decimating…")
                    decimate(ms, target_count)
                    repair_non_manifold(ms)
                    if fill_holes:
                        close_holes_safe(ms, max_hole_size, "Post-decimate")

        topology_log(ms, "Final")
        if topo(ms)["boundary_edges"] > 0:
            if fill_holes or mode == "sdf":
                log(f"Warning: open boundary remains — holes larger than Max "
                    f"Hole Size ({max_hole_size} edges) are not filled. Raise "
                    "Max Hole Size or run Mesh Repair.")
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

    # Last line of defence: a single non-finite vertex makes the whole model
    # invisible in a viewer, which reads as "the remesher ate my model".
    result = _strip_non_finite(result)
    if len(result.faces) == 0:
        error("mesh-remesher: produced an empty mesh — nothing to export")
        return

    # ---- texture transfer for the non-Blender routes --------------------------
    transfer_wanted = (transfer_texture and blender is not None
                       and _has_base_colour_texture(input_path))
    if transfer_wanted:
        geom_path = str(out_dir / f"mesh-remesher-{stamp}_geom.glb")
        result.export(geom_path)
        try:
            progress(93, "Baking texture transfer…")
            _run_blender(blender, HERE / "blender_texture_transfer.py",
                         [input_path, geom_path, out_path],
                         out_dir, "texture-transfer")
            if not Path(out_path).is_file():
                raise RuntimeError("transfer produced no file")
        except Exception as exc:
            log(f"Texture transfer failed ({exc}); keeping the untextured mesh")
            shutil.copy2(geom_path, out_path)
    else:
        result.export(out_path)
        if transfer_texture and blender is None:
            log("Texture transfer skipped: Blender was not found.")
        elif transfer_texture:
            log("Texture transfer skipped: the input has no base-colour texture.")

    log(f"Output: {out_path} ({len(result.faces)} faces)")
    progress(100, "Done")
    done(out_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        error(f"{exc}\n{traceback.format_exc()}")
