"""Pure helpers shared by the detail bake.

Vendored from image-to-3dlab's ``scripts/blender_bake_normals.py`` (Apache-2.0,
Bingeljell) for the Modly mesh-finisher node — only the parts ``i3d_bake_detail.py``
imports, with the CLI/socket machinery left out:

* ``axis_size_ratios`` / ``best_permutation`` — decide whether two meshes are the same
  shape in a given axis mapping. All three ratios land near 1.0 only when the mapping
  is right, which makes this a direct test for orientation rather than a guess.
* ``resolve_tangent_sign`` — negate normal-map texels whose normal points into the
  surface. Generated meshes are inconsistently wound, so some bake rays come back
  reversed; the sign is recoverable afterwards.
"""

from __future__ import annotations

# glTF is Y-up and Blender is Z-up, so the importer maps (x, y, z) -> (x, -z, y).
# A PLY written straight out of a generator never goes through that conversion.
AXIS_PERMUTATIONS = {
    "none": (0, 1, 2),
    "gltf_to_blender": (0, 2, 1),
}


def axis_size_ratios(
    high_size: tuple[float, float, float],
    low_size: tuple[float, float, float],
    permutation: tuple[int, int, int],
) -> tuple[float, float, float]:
    """Per-axis size ratio after permuting the high-poly's axes."""
    permuted = tuple(high_size[i] for i in permutation)
    return tuple(
        (low_size[i] / permuted[i]) if permuted[i] > 1e-9 else 0.0 for i in range(3)
    )


def best_permutation(
    high_size: tuple[float, float, float],
    low_size: tuple[float, float, float],
) -> tuple[str, float]:
    """Pick the axis mapping whose size ratios are most uniform.

    Returns the name and the spread (max ratio / min ratio). A spread near 1.0 means
    the two meshes really are the same shape in that orientation; a large spread means
    neither candidate fits and the bake should not be trusted.
    """
    best_name, best_spread = None, float("inf")
    for name, perm in AXIS_PERMUTATIONS.items():
        ratios = axis_size_ratios(high_size, low_size, perm)
        if min(ratios) <= 0:
            continue
        spread = max(ratios) / min(ratios)
        if spread < best_spread:
            best_name, best_spread = name, spread
    if best_name is None:
        raise ValueError("no usable axis permutation; is one mesh degenerate?")
    return best_name, best_spread


def resolve_tangent_sign(pixels):
    """Negate texels whose normal points into the surface, and say how many.

    Returns (pixels, flipped_fraction). A tangent-space normal has Z > 0 by
    construction: it is a deviation *from* the surface, not through it.
    """
    import numpy as np

    normals = np.asarray(pixels, dtype=np.float32) / 255.0 * 2.0 - 1.0
    flipped = normals[..., 2] < 0.0
    normals[flipped] = -normals[flipped]
    encoded = np.clip((normals + 1.0) / 2.0 * 255.0, 0, 255).astype(np.uint8)
    return encoded, float(flipped.mean())
