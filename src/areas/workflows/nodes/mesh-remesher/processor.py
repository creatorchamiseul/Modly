"""
Mesh Remesher — built-in process extension.

Protocol: reads one JSON line from stdin, writes JSON lines to stdout.
  stdin : { input, params, workspaceDir, tempDir }
  stdout: { type: "progress"|"log"|"done"|"error", ... }
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


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
    target_edge_length = float(params.get("target_edge_length", 0.0))
    target_count = int(float(params.get("target_count", 0) or 0))

    out_dir = Path(workspace_dir) / "Workflows"
    out_dir.mkdir(parents=True, exist_ok=True)
    from time import time
    out_path = str(out_dir / f"mesh-remesher-{int(time() * 1000)}.glb")

    log(f"Mode: {mode}, edge length: {target_edge_length or 'auto'}, target count: {target_count or 'off'}")

    if mode == "none":
        progress(50, "Passing through…")
        shutil.copy2(input_path, out_path)
        progress(100, "Done")
        done(out_path)
        return

    try:
        import pymeshlab
    except ImportError:
        error("mesh-remesher: pymeshlab is not available on this system")
        return

    import trimesh

    progress(10, "Loading mesh…")
    loaded = trimesh.load(input_path)
    if isinstance(loaded, trimesh.Scene):
        geoms = list(loaded.geometry.values())
        geom  = trimesh.util.concatenate(geoms) if len(geoms) > 1 else geoms[0]
    else:
        geom = loaded

    if target_count > 0 and mode != "none":
        area = float(geom.area) if geom.area else 0.0
        if area > 0:
            target_edge_length = (area / (0.4325 * target_count * 1.15)) ** 0.5
            log(f"Target count {target_count}: area {area:.6f} -> edge length {target_edge_length:.6f}")

    tmp_dir = tempfile.mkdtemp()
    try:
        ply_in  = os.path.join(tmp_dir, "input.ply")
        ply_out = os.path.join(tmp_dir, "output.ply")
        geom.export(ply_in)

        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(ply_in)

        if target_edge_length <= 0:
            measures           = ms.get_geometric_measures()
            target_edge_length = float(measures.get("avg_edge_length", 0.02))
            log(f"Auto edge length: {target_edge_length:.6f}")

        progress(30, f"Remeshing ({mode})…")

        if mode == "triangle":
            ms.meshing_isotropic_explicit_remeshing(
                targetlen=pymeshlab.PureValue(target_edge_length),
                iterations=3,
            )
        elif mode == "quad":
            ms.meshing_isotropic_explicit_remeshing(
                targetlen=pymeshlab.PureValue(target_edge_length),
                iterations=3,
            )
            try:
                ms.generate_polygonal_mesh()
                ms.meshing_poly_to_tri()
            except Exception:
                pass

        if target_count > 0 and mode != "none":
            current_faces = int(ms.current_mesh().face_number())
            if current_faces > target_count:
                try:
                    ms.meshing_decimation_quadric_edge_collapse(
                        targetfacenum=int(target_count),
                        qualitythr=0.3,
                        preserveboundary=True,
                        optimalplacement=True,
                    )
                except Exception:
                    ms.meshing_decimation_quadric_edge_collapse(targetfacenum=int(target_count))
                log(f"Decimated {current_faces} -> {int(ms.current_mesh().face_number())} faces")
        progress(80, "Exporting…")
        ms.save_current_mesh(ply_out)
        result = trimesh.load(ply_out, force="mesh")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    result.export(out_path)
    log(f"Output: {out_path} ({len(result.faces)} faces)")
    progress(100, "Done")
    done(out_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        error(f"{exc}\n{traceback.format_exc()}")
