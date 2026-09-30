"""Mesh Finisher — Modly process extension (the "Finish" port).

Turns a generated high-poly asset into a game-ready one, the same route image-to-3dlab
runs by hand (Apache-2.0, Bingeljell — https://github.com/Bingeljell/image-to-3dlab):

1. retopologise  (Blender)  weld -> voxel remesh -> QuadriFlow or decimate -> smart UV
                            -> bake the original texture across.
2. Pixel Match   (numpy)    fit a camera to the source photo's silhouette and paint the
                            photo's real pixels onto every surface it can see; text and
                            logos survive instead of being redrawn as lookalikes.
3. bake detail   (Blender)  normal map + metallic-roughness from the original high-poly.
4. compress      (Pillow)   re-encode textures (colour capped at the atlas size, data
                            maps 1024, JPEG q90).

Protocol: one JSON line on stdin { input, params, nodeId, workspaceDir, tempDir };
JSON lines on stdout { type: "progress" | "log" | "done" | "error", ... }.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import i3d_blender_find as bf  # noqa: E402

ANGLES_DEFAULT = 89.0  # Blender Smart UV angle limit, as image-to-3dlab tuned it
IOR_DEFAULT = 1.45


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def progress(pct: float, label: str) -> None:
    emit({"type": "progress", "percent": max(0, min(100, int(pct))), "label": label})


def log(msg: str) -> None:
    emit({"type": "log", "message": str(msg)[:1000]})


def done(file_path: str) -> None:
    emit({"type": "done", "result": {"filePath": file_path}})


def error(msg: str) -> None:
    emit({"type": "error", "message": str(msg)[:4000]})


def _flag(params: dict, key: str, default: bool = True) -> bool:
    value = params.get(key, "yes" if default else "no")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"yes", "on", "true", "1"}


def _number(params: dict, key: str, default: float) -> float:
    try:
        return float(params.get(key, default))
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


STAGE_RELAY_PREFIXES = ("RETOPO::", "BAKE_DETAIL::")


def _blender_env(blender: Path) -> dict:
    """Environment for a Blender stage, keeping its user files inside the app folder.

    When the executable lives under a ``.tools`` directory (the portable install this
    node expects), Blender's config, scripts and temp files are pointed back into that
    same folder tree, so a headless run adds nothing to the user's home drive.
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


def _run_blender(blender: Path, script: Path, args: list, log_path: Path) -> None:
    """Run one headless Blender stage, teeing output to a log file, relaying markers."""
    command = [str(blender), "--background", "--factory-startup",
               "--python", str(script), "--", *[str(a) for a in args]]
    log(f"Blender: {script.name} ...")
    env = _blender_env(blender)
    tail: list[str] = []
    with log_path.open("w", encoding="utf-8", errors="replace") as handle:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            handle.write(line)
            stripped = line.rstrip()
            if stripped:
                tail.append(stripped)
                del tail[:-40]
                if stripped.startswith(STAGE_RELAY_PREFIXES):
                    log(stripped[:500])
        code = process.wait()
    if code != 0:
        raise RuntimeError(
            f"{script.name} failed (exit {code}); last lines:\n"
            + "\n".join(tail[-15:])
        )


def _photo_paint_stage(model: Path, image_path: Path, output: Path,
                       weights_png: Path) -> dict:
    """Paint the model's texture with the source photo's real pixels (Pixel Match).

    Returns a record for the finish log. Raises when the camera fit is too weak or the
    GLB is not in the one-mesh/one-material shape the paint expects; the caller then
    keeps the transferred texture instead.
    """
    import numpy as np
    from PIL import Image

    import i3d_camera_fit as fit
    import i3d_photo_paint as pp

    positions, uvs, faces, texture = pp.read_glb(model)
    image = np.asarray(Image.open(image_path).convert("RGBA"))
    view, iou, info = fit.fit_camera(positions, faces, image)
    if view is None:
        raise RuntimeError(f"camera fit impossible ({info.get('reason', 'no fit')})")
    if iou < fit.MIN_IOU:
        raise RuntimeError(
            f"camera fit too weak (silhouette IoU {iou:.2f} < {fit.MIN_IOU}); "
            "keeping the transferred texture"
        )
    painted, weight = pp.paint_texture(
        texture, positions, uvs, faces, [view], mesh_scale=1.0,
        frame=((0, 1, 2), (1, 1, 1)),
    )
    output.write_bytes(pp.replace_base_colour(model.read_bytes(), pp.encode_png(painted)))
    Image.fromarray(np.rint(weight * 255).astype(np.uint8)).save(weights_png)
    share = float((weight > 0.5).mean())
    log(f"Pixel Match: IoU {iou:.2f}, {share:.0%} of the texture now from the photo "
        f"(debug map: {weights_png.name})")
    return {"iou": round(iou, 3), "texture_share": round(share, 3), **info}


def main() -> None:
    raw = sys.stdin.readline()
    data = json.loads(raw)

    input_data = data.get("input", {}) or {}
    params = data.get("params", {}) or {}
    workspace = Path(data.get("workspaceDir") or ".")

    input_path = input_data.get("filePath")
    if not input_path or not Path(input_path).is_file():
        error(f"mesh-finisher: input file not found: {input_path}")
        return
    input_path = Path(input_path)

    target_faces = int(_clamp(_number(params, "target_faces", 40000), 1000, 200000))
    voxel_fraction = _clamp(_number(params, "voxel_fraction", 0.004), 0.0, 0.05)
    atlas = int(_number(params, "texture_size", 2048))
    if atlas not in (1024, 2048, 4096):
        atlas = 2048
    metallic = _clamp(_number(params, "metallic", 0.25), 0.0, 1.0)
    roughness = _clamp(_number(params, "roughness", 0.65), 0.0, 1.0)
    pixel_match = str(params.get("pixel_match", "auto")).strip().lower() != "off"
    bake_detail = _flag(params, "bake_detail", True)
    compress = _flag(params, "compress", True)

    images = [
        Path(p) for p in (params.get("extra_image_paths") or [])
        if isinstance(p, str) and Path(p).is_file()
    ]

    blender = bf.find_blender()
    if blender is None:
        error(bf.missing_help())
        return
    version = bf.blender_version(blender)
    problem = bf.version_problem(version)
    if problem:
        log(problem)
    version_text = f"{version[0]}.{version[1]}" if version else "unknown"
    log(f"Blender {version_text}: {blender}")

    out_dir = workspace / "Workflows" / f"mesh-finisher-{int(time.time() * 1000)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    step_retopo = out_dir / "1_retopo.glb"
    step_photo = out_dir / "2_photo.glb"
    step_baked = out_dir / "3_baked.glb"
    final_path = out_dir / "final.glb"
    weights_png = out_dir / "photo_weights.png"

    record: dict = {
        "source": str(input_path),
        "image": str(images[0]) if images else None,
        "stage_order": ["retopologise"]
        + (["photo"] if (pixel_match and images) else [])
        + (["bake"] if bake_detail else [])
        + (["compress"] if compress else []),
        "retopology": {"faces": target_faces, "atlas": atlas, "voxel": voxel_fraction,
                       "angle": ANGLES_DEFAULT},
        "surface": {"metallic": metallic, "roughness": roughness, "ior": IOR_DEFAULT},
        "seconds": {},
        "done": False,
    }

    started = time.time()
    try:
        # ---- 1. retopologise ------------------------------------------------------
        progress(5, f"Retopologising to {target_faces:,} faces (Blender)")
        mark = time.time()
        _run_blender(
            blender, HERE / "i3d_retopo_bake.py",
            [input_path, step_retopo, target_faces, atlas, ANGLES_DEFAULT,
             voxel_fraction, metallic, roughness, IOR_DEFAULT],
            out_dir / "1_retopo.log",
        )
        record["seconds"]["retopologise"] = round(time.time() - mark, 1)
        current = step_retopo

        # ---- 2. Pixel Match -------------------------------------------------------
        if pixel_match and images:
            progress(50, "Pixel Match: fitting the camera to the source photo")
            mark = time.time()
            try:
                record["photo"] = _photo_paint_stage(current, images[0], step_photo,
                                                     weights_png)
                current = step_photo
                record["seconds"]["photo"] = round(time.time() - mark, 1)
            except Exception as exc:  # keep going: the transferred texture stands in
                log(f"Pixel Match skipped: {exc}")
                record["photo"] = {"skipped": str(exc)}
        elif pixel_match:
            log("Pixel Match: no Source Photo connected; skipped.")
            record["photo"] = {"skipped": "no image input"}

        # ---- 3. bake detail -------------------------------------------------------
        if bake_detail:
            progress(65, "Baking normal map + surface material (Blender)")
            mark = time.time()
            _run_blender(
                blender, HERE / "i3d_bake_detail.py",
                [input_path, current, step_baked, atlas],
                out_dir / "3_bake.log",
            )
            record["seconds"]["bake"] = round(time.time() - mark, 1)
            current = step_baked

        # ---- 4. compress ----------------------------------------------------------
        if compress:
            progress(90, f"Compressing textures (atlas {atlas}px)")
            mark = time.time()
            import i3d_compress_glb as compress_glb

            data_bytes = current.read_bytes()
            out_bytes, report = compress_glb.compress(
                data_bytes, max_size=atlas, map_size=1024,
            )
            final_path.write_bytes(out_bytes)
            record["seconds"]["compress"] = round(time.time() - mark, 1)
            record["textures"] = report
            log(f"Compressed {len(data_bytes) / 1048576:.1f} -> "
                f"{len(out_bytes) / 1048576:.1f} MB")
        else:
            shutil.copy2(current, final_path)

        record["done"] = True
        record["total_seconds"] = round(time.time() - started, 1)
        record["size_bytes"] = final_path.stat().st_size
        (out_dir / "finish-record.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8")
        progress(100, f"Finished ({record['size_bytes'] / 1048576:.1f} MB)")
        done(str(final_path))
    except Exception as exc:
        import traceback

        log(traceback.format_exc())
        error(f"mesh-finisher: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — the protocol wants errors as JSON too
        import traceback

        error(f"{exc}\n{traceback.format_exc()}")
