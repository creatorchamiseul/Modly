# Modly quad remesh — Blender headless script for the mesh-remesher node ("sdf" route).
#
#   blender --background --factory-startup --python quad_remesh.py -- \
#       IN.glb OUT.glb OUT_quads.obj [target_quads] [transfer(0/1)] [voxel_hint]
#
# Why a voxel pass: the inputs are shard-soup AI exports (this project measured
# one at ~840k open boundary edges). A voxel resample rebuilds one closed
# surface from the volume and the result is *quad topology by construction* —
# uniform, watertight, and it cannot tear. Finer voxels keep thin features,
# coarser fuses them; the target count picks the size, tried up to three times
# against the actual result so the count lands close.
#
# The texture is transferred by baking the original's albedo into the new UVs
# (Smart UV Project + a selected-to-active emit bake — the same transfer the
# Finish node uses). The GLB is triangulated by the format; the true quad mesh
# is written beside it as an .obj.
#
# This file is standalone: it only needs Blender's own Python (mathutils, bpy).

from __future__ import annotations

import json
import math
import sys

import bpy


def parse_args(argv: list[str]) -> tuple[str, str, str, int, bool, float, str]:
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    if len(argv) < 3:
        raise SystemExit(
            "usage: blender --background --python quad_remesh.py -- "
            "IN.glb OUT.glb OUT_quads.obj [target_quads] [transfer 0/1] [voxel_hint] [denoised_tex.png]"
        )
    target = int(argv[3]) if len(argv) > 3 and argv[3] else 0
    transfer = (argv[4] != "0") if len(argv) > 4 else True
    voxel_hint = float(argv[5]) if len(argv) > 5 and argv[5] else 0.0
    denoise_tex = argv[6] if len(argv) > 6 and argv[6] and argv[6] != "-" else ""
    return argv[0], argv[1], argv[2], target, transfer, voxel_hint, denoise_tex


def clear_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def base_colour_image(material):
    """The image feeding a glTF material's Base Color, or None."""
    if not material or not material.use_nodes:
        return None
    for node in material.node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        socket = node.inputs.get("Base Color")
        if socket and socket.is_linked:
            upstream = socket.links[0].from_node
            if upstream.type == "TEX_IMAGE" and upstream.image:
                return upstream.image
    for node in material.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image:
            return node.image
    return None


def surface_area(obj) -> float:
    return float(sum(p.area for p in obj.data.polygons))


def keep_main_parts(obj, min_faces_min: int = 64, fraction: float = 500.0) -> tuple[int, int]:
    """Drop micro loose parts; returns (kept, dropped).

    A voxel pass can leave distant shards as separate blobs — visible as
    floating specks in a viewer. Keep the parts that carry the model.
    """
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.delete_loose(use_verts=True, use_edges=True, use_faces=False)
    bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")
    parts = [o for o in bpy.context.scene.objects
             if o.type == "MESH" and o.name.startswith("QUADR")]
    parts.sort(key=lambda o: len(o.data.polygons), reverse=True)
    cutoff = max(min_faces_min, int(len(parts[0].data.polygons) / fraction)) if parts else 0
    dropped = 0
    for part in parts[1:]:
        if len(part.data.polygons) < cutoff:
            bpy.data.objects.remove(part, do_unlink=True)
            dropped += 1
    parts = [o for o in bpy.context.scene.objects
             if o.type == "MESH" and o.name.startswith("QUADR")]
    bpy.ops.object.select_all(action="DESELECT")
    for part in parts:
        part.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    if len(parts) > 1:
        bpy.ops.object.join()
    joined = bpy.context.view_layer.objects.active
    joined.name = "QUADR"
    return len(parts), dropped


def strip_base_sheet(obj) -> None:
    """Delete the flat ground sheet AI exports sometimes bake into the base.

    Detected as a fan of near-horizontal faces sitting in the lowest
    millimetres of the model. Without this the voxel pass turns the sheet
    into a giant slab that merges with the model (the cone touches it), and
    the result renders with a 1x1 m "floor" stuck underneath.
    """
    import numpy as np

    total_removed = 0
    for _ in range(4):
        me = obj.data
        nv = len(me.vertices)
        nf = len(me.polygons)
        co = np.empty(nv * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        zmin = float(co[:, 2].min())
        height = max(float(co[:, 2].max()) - zmin, 1e-9)

        centers = np.empty(nf * 3, dtype=np.float32)
        me.polygons.foreach_get("center", centers)
        centers = centers.reshape(-1, 3)
        normals = np.empty(nf * 3, dtype=np.float32)
        me.polygons.foreach_get("normal", normals)
        normals = normals.reshape(-1, 3)

        band = max(0.02 * height, 0.008)
        radius = np.hypot(centers[:, 0], centers[:, 1])
        low = centers[:, 2] < zmin + band
        flat = np.abs(normals[:, 2]) > 0.6
        hit = low & (flat | (radius > 0.1))
        n_hit = int(hit.sum())
        if n_hit < 50:  # nothing sheet-like left; done
            break
        if n_hit > 0.35 * nf:
            print(f"QUADRS:: base sheet check matched {n_hit:,} of {nf:,} faces"
                  f" — aborting removal", flush=True)
            return
        # Deselect everything first: a stale select-all from the weld pass would
        # otherwise flush back in when entering edit mode and delete the whole mesh.
        zeros = np.zeros(nv, dtype=bool)
        me.vertices.foreach_set("select", zeros)
        me.edges.foreach_set("select", np.zeros(len(me.edges), dtype=bool))
        sel = np.zeros(nf, dtype=bool)
        sel[hit] = True
        me.polygons.foreach_set("select", sel)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.delete(type="FACE")
        bpy.ops.object.mode_set(mode="OBJECT")
        total_removed += n_hit

    if total_removed:
        print(f"QUADRS:: removed base sheet ({total_removed:,} faces total)", flush=True)
    else:
        print("QUADRS:: no base sheet detected", flush=True)


def repair_cap_uvs(obj, nv_before: int, nl_before: int) -> int:
    """Continue the rim UVs across the faces that fill_holes just added.

    Blender leaves fill faces at UV (0,0); baked, they sample one atlas corner
    texel and read as flat blocks or holes in the final texture. Each new loop
    copies the UV of the nearest pre-fill vertex, so the texture smears
    smoothly from the rim across the cap instead.
    """
    import numpy as np
    import mathutils

    me = obj.data
    uv = me.uv_layers.active
    nloop = len(me.loops)
    if uv is None or nloop <= nl_before:
        return 0
    lv = np.empty(nloop, dtype=np.int32)
    me.loops.foreach_get("vertex_index", lv)
    uvflat = np.empty(nloop * 2, dtype=np.float32)
    uv.data.foreach_get("uv", uvflat)
    uvflat = uvflat.reshape(-1, 2)

    uniq, first_idx = np.unique(lv[:nl_before], return_index=True)
    first_uv = uvflat[first_idx]

    nv = min(nv_before, len(me.vertices))
    kd = mathutils.kdtree.KDTree(nv)
    for i in range(nv):
        kd.insert(me.vertices[i].co, i)
    kd.balance()

    repaired = 0
    for li in range(nl_before, nloop):
        _, nidx, _ = kd.find(me.vertices[int(lv[li])].co)
        pos = int(np.searchsorted(uniq, nidx))
        if pos < len(uniq) and uniq[pos] == nidx:
            uvflat[li] = first_uv[pos]
            repaired += 1
    if repaired:
        uv.data.foreach_set("uv", uvflat.reshape(-1))
    return repaired


def _box_mean(a, r: int):
    import numpy as np

    p = np.pad(a, r, mode="edge")
    c = p.cumsum(axis=0).cumsum(axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    k = 2 * r + 1
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s / float(k * k)


def despeckle_image(img, passes=((20.0, 4), (13.0, 6), (9.0, 8))) -> int:
    """Remove the dark dot-noise the albedo carries (they read as pits/holes).

    The AI albedo is sprinkled with sub-pixel dark specks; baked 1:1 onto the
    new surface every one of them shows up as a tiny pit. Replace pixels that
    sit much darker than their local box mean with that mean, escalating in
    three radius steps. Works on the image's float buffer, in place.
    """
    import numpy as np

    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    rgba = px.reshape(h, w, 4)
    rgb = rgba[:, :, :3]
    total = 0
    for delta, r in passes:
        lum = rgb.mean(axis=2)
        m = _box_mean(lum, r)
        speck = (lum + delta / 255.0 < m) & (m > 0.23)
        if not speck.any():
            continue
        if speck.mean() > 0.25:
            continue  # runaway guard
        for c in range(3):
            cm = _box_mean(rgb[:, :, c], r + 2)
            rgb[:, :, c] = np.where(speck, cm, rgb[:, :, c])
        total += int(speck.sum())
    if total:
        img.pixels.foreach_set(rgba.reshape(-1))
    return total


def main() -> None:
    in_path, out_glb, out_obj, target, transfer, voxel_hint, denoise_tex = parse_args(list(sys.argv))
    clear_scene()
    bpy.ops.import_scene.gltf(filepath=in_path)

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit(f"no mesh in {in_path}")
    source = max(meshes, key=lambda o: len(o.data.polygons))
    if len(meshes) > 1:
        bpy.ops.object.select_all(action="DESELECT")
        for o in meshes:
            o.select_set(True)
        bpy.context.view_layer.objects.active = source
        bpy.ops.object.join()

    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=1e-6)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")

    # Drop the ground sheet first so the hole-fill below cannot resurrect it
    # as a solid slab; then cap the swiss-cheese rims so the volume is solid.
    strip_base_sheet(source)

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    try:
        me = source.data
        nv_before, nl_before = len(me.vertices), len(me.loops)
        bpy.ops.mesh.fill_holes(sides=0)
        bpy.ops.object.mode_set(mode="OBJECT")
        # Fresh fill faces arrive with UV (0,0) — baked, they sample the atlas
        # corner and read as flat blocks/holes in the texture. Continue the
        # rim's UVs across them so the pattern smears smoothly instead.
        repaired = repair_cap_uvs(source, nv_before, nl_before)
        if repaired:
            print(f"QUADRS:: cap UV repair: {repaired:,} loops continued from the rim", flush=True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.remove_doubles(threshold=1e-6)
        bpy.ops.mesh.normals_make_consistent(inside=False)
        print("QUADRS:: source hole-fill pass done", flush=True)
    except Exception as exc:
        print(f"QUADRS:: source hole fill skipped ({exc})", flush=True)
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass

    image = base_colour_image(source.data.materials[0] if source.data.materials else None)
    print(f"QUADRS:: source {len(source.data.polygons):,} faces, "
          f"texture={'yes' if image is not None else 'no'}", flush=True)

    # A smoothed stand-in of the source for the detail pass: the AI export's
    # surface carries a high-frequency crinkle (a dimple field), and projecting
    # straight onto it imprints that noise as pits. Macro shapes survive the
    # smoothing; the crinkle does not.
    proxy = source.copy()
    proxy.data = source.data.copy()
    proxy.name = "PROXY"
    bpy.context.scene.collection.objects.link(proxy)
    bpy.ops.object.select_all(action="DESELECT")
    proxy.select_set(True)
    bpy.context.view_layer.objects.active = proxy
    pre_smooth = proxy.modifiers.new("pre-smooth", "SMOOTH")
    pre_smooth.factor = 0.5
    pre_smooth.iterations = 12
    bpy.ops.object.modifier_apply(modifier=pre_smooth.name)
    print("QUADRS:: smoothed proxy prepared (source denoise pass)", flush=True)

    quad = source.copy()
    quad.data = source.data.copy()
    quad.name = "QUADR"
    quad.data.materials.clear()
    bpy.context.scene.collection.objects.link(quad)

    area = surface_area(quad)
    max_dim = max(quad.dimensions)
    if voxel_hint > 0:
        voxel = voxel_hint
    elif target > 0 and area > 0:
        voxel = math.sqrt(area / float(target))
    else:
        voxel = max_dim * 0.004
    voxel = max(voxel, max_dim / 512.0)
    voxel = min(voxel, max_dim / 24.0)

    base_data = quad.data.copy()
    for attempt in range(3):
        quad.data = base_data.copy()
        quad.data.remesh_voxel_size = voxel
        quad.data.remesh_voxel_adaptivity = 0.0
        bpy.ops.object.select_all(action="DESELECT")
        quad.select_set(True)
        bpy.context.view_layer.objects.active = quad
        print(f"QUADRS:: voxel remesh at {voxel:.5f} (attempt {attempt + 1}) ...", flush=True)
        bpy.ops.object.voxel_remesh()
        faces_now = len(quad.data.polygons)
        print(f"QUADRS:: extracted {faces_now:,} faces", flush=True)
        if target <= 0 or faces_now == 0:
            break
        ratio = faces_now / float(target)
        if 0.7 <= ratio <= 1.45 or attempt == 2:
            break
        voxel *= math.sqrt(ratio)
        voxel = max(voxel, max_dim / 512.0)
        voxel = min(voxel, max_dim / 24.0)

    if len(quad.data.polygons) == 0:
        raise SystemExit("QUADRS:: voxel remesh produced nothing — raise the target")

    kept, dropped = keep_main_parts(quad)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=1e-6)
    bpy.ops.mesh.fill_holes(sides=0)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    total = len(quad.data.polygons)
    quads = sum(1 for p in quad.data.polygons if len(p.vertices) == 4)
    print(f"QUADRS:: cleaned: kept {kept} part(s), dropped {dropped}, "
          f"{total:,} faces ({100.0 * quads / max(total, 1):.1f}% quads)", flush=True)

    # Restore the detail the voxel grid melted: pull every vertex onto the
    # original surface's nearest point. Moves vertices only — the all-quad
    # topology (and watertightness) is untouched.
    try:
        bpy.ops.object.select_all(action="DESELECT")
        quad.select_set(True)
        bpy.context.view_layer.objects.active = quad
        wrap = quad.modifiers.new("rewrap", "SHRINKWRAP")
        wrap.target = proxy
        wrap.wrap_method = "NEAREST_SURFACEPOINT"
        wrap.offset = 0.0
        bpy.ops.object.modifier_apply(modifier=wrap.name)
        print("QUADRS:: shrink-wrapped onto the smoothed proxy", flush=True)
    except Exception as exc:
        print(f"QUADRS:: shrinkwrap skipped ({exc})", flush=True)

    # Micro-denoise: the shrinkwrap imprints the source's fine surface crinkle,
    # which shades as speckle in a viewer. A light Laplacian pass calms it
    # without touching the macro shape (moves vertices only; quads intact).
    try:
        bpy.ops.object.select_all(action="DESELECT")
        quad.select_set(True)
        bpy.context.view_layer.objects.active = quad
        denoise = quad.modifiers.new("denoise", "SMOOTH")
        denoise.factor = 0.5
        denoise.iterations = 2
        bpy.ops.object.modifier_apply(modifier=denoise.name)
        print("QUADRS:: micro-denoised (smooth x2)", flush=True)
    except Exception as exc:
        print(f"QUADRS:: denoise skipped ({exc})", flush=True)

    # Smooth shading: flat-shaded quads read as a speckly, faceted surface.
    try:
        bpy.ops.object.shade_smooth()
    except Exception as exc:
        print(f"QUADRS:: shade smooth skipped ({exc})", flush=True)

    if out_obj and out_obj != "-":
        bpy.ops.object.select_all(action="DESELECT")
        quad.select_set(True)
        bpy.context.view_layer.objects.active = quad
        try:
            bpy.ops.wm.obj_export(filepath=out_obj, export_selected_objects=True,
                                  export_triangulated_mesh=False, export_materials=False)
        except TypeError:
            bpy.ops.wm.obj_export(filepath=out_obj, export_selected_objects=True)
        print(f"QUADRS:: wrote {out_obj} (true quads)", flush=True)

    baked = False
    if transfer and image is not None:
        bpy.ops.object.select_all(action="DESELECT")
        quad.select_set(True)
        bpy.context.view_layer.objects.active = quad
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(89.0), island_margin=0.003)
        bpy.ops.object.mode_set(mode="OBJECT")

        # Bake at twice the source resolution (capped at 4096): the new atlas
        # trades texel efficiency for quad topology, and baking at the source
        # size magnifies the source texture's fine detail (e.g. paint speckle)
        # by that loss factor. Doubling keeps on-surface texel size at parity.
        source_size = int(image.size[0]) or 1024
        size = max(1024, min(4096, source_size * 2))
        target_image = bpy.data.images.new("quad_albedo", width=size, height=size, alpha=False)
        dst = bpy.data.materials.new("QUAD_MAT")
        dst.use_nodes = True
        dnodes, dlinks = dst.node_tree.nodes, dst.node_tree.links
        dst_tex = dnodes.new("ShaderNodeTexImage")
        dst_tex.image = target_image
        quad.data.materials.append(dst)
        quad.active_material_index = 0
        for node in dnodes:
            node.select = False
        dst_tex.select = True
        dnodes.active = dst_tex

        emit = bpy.data.materials.new("EMIT_SRC")
        emit.use_nodes = True
        enodes, elinks = emit.node_tree.nodes, emit.node_tree.links
        for node in list(enodes):
            if node.type != "OUTPUT_MATERIAL":
                enodes.remove(node)
        output = next(n for n in enodes if n.type == "OUTPUT_MATERIAL")
        bake_image = image
        if denoise_tex:
            try:
                bake_image = bpy.data.images.load(denoise_tex)
                print(f"QUADRS:: using denoised source texture ({denoise_tex})", flush=True)
            except Exception as exc:
                print(f"QUADRS:: denoised texture load failed ({exc}); using the original", flush=True)
        tex = enodes.new("ShaderNodeTexImage")
        tex.image = bake_image
        tex.interpolation = "Linear"
        emission = enodes.new("ShaderNodeEmission")
        elinks.new(tex.outputs["Color"], emission.inputs["Color"])
        elinks.new(emission.outputs["Emission"], output.inputs["Surface"])
        # Bake *from the smoothed proxy*, not the raw crinkled source: the quad
        # surface was shrink-wrapped onto the proxy, so ray distances stay tiny
        # and the bake cannot miss (misses were coming out as black gashes —
        # the "old holes frozen into the texture" the viewer shows). The proxy
        # carries the same atlas (now including the repaired cap UVs).
        proxy.data.materials.clear()
        proxy.data.materials.append(emit)

        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        try:
            scene.cycles.device = "CPU"
            scene.cycles.samples = 1
        except AttributeError:
            pass
        bake = scene.render.bake
        bake.use_selected_to_active = True
        bake.margin = 16
        try:
            bake.margin_type = "ADJACENT_FACES"
        except Exception:
            pass
        bake.use_clear = True
        reach = max(quad.dimensions) * 0.03
        bake.cage_extrusion = reach
        bake.max_ray_distance = reach * 3.0

        bpy.ops.object.select_all(action="DESELECT")
        proxy.select_set(True)
        quad.select_set(True)
        bpy.context.view_layer.objects.active = quad
        print("QUADRS:: baking texture transfer ...", flush=True)
        bpy.ops.object.bake(type="EMIT")

        try:
            removed = despeckle_image(target_image)
            if removed:
                print(f"QUADRS:: albedo despeckle: {removed:,} dot pixels cleaned", flush=True)
        except Exception as exc:
            print(f"QUADRS:: albedo despeckle skipped ({exc})", flush=True)

        principled = dnodes.new("ShaderNodeBsdfPrincipled")
        doutput = next(n for n in dnodes if n.type == "OUTPUT_MATERIAL")
        dlinks.new(dst_tex.outputs["Color"], principled.inputs["Base Color"])
        principled.inputs["Metallic"].default_value = 0.0
        principled.inputs["Roughness"].default_value = 0.6
        dlinks.new(principled.outputs["BSDF"], doutput.inputs["Surface"])
        baked = True
    elif transfer:
        print("QUADRS:: no base-colour texture found; kept an untextured material", flush=True)
    if not baked:
        dst = bpy.data.materials.new("QUAD_MAT")
        dst.use_nodes = True
        dst.node_tree.nodes["Principled BSDF"].inputs["Metallic"].default_value = 0.0
        dst.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.6
        quad.data.materials.append(dst)

    if source.name in bpy.data.objects:
        bpy.data.objects.remove(source, do_unlink=True)
    bpy.ops.object.select_all(action="DESELECT")
    quad.select_set(True)
    bpy.context.view_layer.objects.active = quad
    bpy.ops.export_scene.gltf(filepath=out_glb, export_format="GLB", use_selection=True)
    print("QUADRS:: " + json.dumps({
        "faces": total, "quads": quads,
        "quad_fraction": round(quads / max(total, 1), 4),
        "voxel": round(voxel, 6), "baked": baked,
    }), flush=True)


if __name__ == "__main__":
    main()
