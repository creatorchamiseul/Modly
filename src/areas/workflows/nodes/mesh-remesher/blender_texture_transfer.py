# Modly texture transfer — Blender headless script for the mesh-remesher node.
#
#   blender --background --factory-startup --python blender_texture_transfer.py -- \
#       SOURCE.glb TARGET.glb OUT.glb
#
# The pymeshlab remeshing routes never carry UVs (their geometry filters drop
# them), so the output would arrive untextured. This bakes SOURCE's base
# colour into TARGET's new UVs (Smart UV Project + a selected-to-active emit
# bake — the same transfer the Finish node uses) and writes OUT: TARGET's
# geometry exactly as it is, plus UVs and the baked material.
#
# The SDF/Blender quad route does its transfer inside quad_remesh.py; this
# script is for the triangle / quad-dominant routes.
#
# Standalone: only Blender's own Python (bpy) is needed.

from __future__ import annotations

import json
import math
import sys

import bpy


def parse_args(argv: list[str]) -> tuple[str, str, str]:
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    if len(argv) < 3:
        raise SystemExit(
            "usage: blender --background --python blender_texture_transfer.py -- "
            "SOURCE.glb TARGET.glb OUT.glb"
        )
    return argv[0], argv[1], argv[2]


def import_meshes(path: str) -> list:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in bpy.data.objects if o not in before and o.type == "MESH"]


def base_colour_image(material):
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


def main() -> None:
    src_path, tgt_path, out_path = parse_args(list(sys.argv))
    bpy.ops.wm.read_factory_settings(use_empty=True)

    sources = import_meshes(src_path)
    targets = import_meshes(tgt_path)
    if not sources or not targets:
        raise SystemExit("TEXFER:: both SOURCE and TARGET must contain a mesh")
    source = max(sources, key=lambda o: len(o.data.polygons))
    target = max(targets, key=lambda o: len(o.data.polygons))

    image = base_colour_image(source.data.materials[0] if source.data.materials else None)
    if image is None:
        raise SystemExit("TEXFER:: no base-colour texture on the source")

    # Weld + orient the source first: rays find a closed, consistently wound
    # surface far more reliably than a seam-split one (the same fix retopo
    # bakes depend on).
    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=1e-6)
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")

    # UVs for the target, then the bake target image on the active slot.
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(89.0), island_margin=0.0002)
    bpy.ops.object.mode_set(mode="OBJECT")

    # The new atlas trades texel efficiency for quad topology; baking at the
    # source size magnifies fine detail (paint speckle, waffle lines) by the
    # loss factor, so double it (capped at 4096) to keep texel size at parity.
    source_size = int(image.size[0]) or 1024
    size = max(1024, min(4096, source_size * 2))
    target_image = bpy.data.images.new("transfer_albedo", width=size, height=size,
                                       alpha=False)
    dst = bpy.data.materials.new("TRANSFER_MAT")
    dst.use_nodes = True
    dnodes, dlinks = dst.node_tree.nodes, dst.node_tree.links
    dst_tex = dnodes.new("ShaderNodeTexImage")
    dst_tex.image = target_image
    target.data.materials.clear()
    target.data.materials.append(dst)
    target.active_material_index = 0
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
    tex = enodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.interpolation = "Closest"
    emission = enodes.new("ShaderNodeEmission")
    elinks.new(tex.outputs["Color"], emission.inputs["Color"])
    elinks.new(emission.outputs["Emission"], output.inputs["Surface"])
    source.data.materials.clear()
    source.data.materials.append(emit)

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
    bake.use_clear = True
    reach = max(target.dimensions) * 0.02
    bake.cage_extrusion = reach
    bake.max_ray_distance = reach * 2.0

    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    print("TEXFER:: baking ...", flush=True)
    bpy.ops.object.bake(type="EMIT")

    principled = dnodes.new("ShaderNodeBsdfPrincipled")
    doutput = next(n for n in dnodes if n.type == "OUTPUT_MATERIAL")
    dlinks.new(dst_tex.outputs["Color"], principled.inputs["Base Color"])
    principled.inputs["Metallic"].default_value = 0.0
    principled.inputs["Roughness"].default_value = 0.6
    dlinks.new(principled.outputs["BSDF"], doutput.inputs["Surface"])

    bpy.data.objects.remove(source, do_unlink=True)
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.export_scene.gltf(filepath=out_path, export_format="GLB", use_selection=True)
    print("TEXFER:: " + json.dumps({"baked": True, "size": size}), flush=True)


if __name__ == "__main__":
    main()
