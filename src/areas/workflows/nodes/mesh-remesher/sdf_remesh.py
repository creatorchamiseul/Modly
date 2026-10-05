"""Modly SDF remesher — a self-contained, hole-free remeshing core (pure numpy).

**Why it cannot tear holes.** Classic marching cubes looks up a per-cube
triangle table; adjacent cubes can disagree about how the surface crosses
their shared face (the face-ambiguity problem) and every disagreement shows
up as a crack or hole. This implementation extracts the surface with
*marching tetrahedra* on a consistent 6-tet (Kuhn) decomposition of every
grid cell: each tetrahedron is cut unambiguously and neighbouring tets agree
on their shared faces, so every extracted edge belongs to exactly two
triangles. The output is closed — watertight by construction — before any
repair pass even runs.

**Pipeline** (all vectorised numpy; no meshing-library remesher involved):

1. normalize the mesh into a unit-diagonal box (precision, robust thresholds)
2. sample the surface densely (points + face normals at ~half the voxel pitch)
3. inside/outside at every grid point by z-ray parity: each triangle adds one
   crossing per column its xy-projection covers; a per-column xor-accumulate
   of crossings gives the parity. Exact for a closed mesh — the processor
   closes every hole before calling.
4. unsigned distance to the surface: multi-sweep chamfer distance transform
5. surface extraction: marching tetrahedra at iso level 0
6. winding fix (triangle normals point from the negative to the positive side)
7. Taubin smoothing (moves vertices only — topology untouched) followed by
   projection of the vertices onto the sampled original surface, restoring
   the detail the voxel grid melted.

Trade-off inherent to every SDF remesher: features thinner than one voxel
are fused or vanish — raise the resolution to keep them.
"""

from __future__ import annotations

import numpy as np

# Kuhn decomposition of the unit cube: six tetrahedra sharing the body
# diagonal 0-6. Face-consistent across neighbouring cells, so shared tet
# faces always produce identical crossings.
CUBE_CORNERS = np.array([
    [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
    [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
])
TETS = np.array([
    [0, 1, 2, 6], [0, 2, 3, 6], [0, 3, 7, 6],
    [0, 7, 4, 6], [0, 4, 5, 6], [0, 5, 1, 6],
])
TET_EDGES = np.array([(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)])


def _case_triangles() -> dict:
    """For each of the 14 sign configurations: triangles as edge-index triples.

    The cut of a tetrahedron (convex) is a single polygon whose boundary
    segments lie on the tet's faces: every face with mixed corner signs
    contributes the segment joining its two crossing edges. Walking those
    face segments yields the true polygon cycle — triangulating anything
    else would disagree with the neighbouring tet across a shared face and
    crack the surface. Winding is fixed globally against the sign field.
    """
    faces4 = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
    table = {}
    for case in range(1, 15):
        positive = {i for i in range(4) if case >> i & 1}
        crossing = [e for e, (a, b) in enumerate(TET_EDGES)
                    if (a in positive) != (b in positive)]
        if len(crossing) == 3:
            table[case] = [tuple(crossing)]
            continue
        # 4 crossings: walk the face segments to recover the quad cycle
        adj = {e: [] for e in crossing}
        for face in faces4:
            seg = [e for e, (a, b) in enumerate(TET_EDGES)
                   if a in face and b in face and (a in positive) != (b in positive)]
            if len(seg) == 2:
                adj[seg[0]].append(seg[1])
                adj[seg[1]].append(seg[0])
        order = [crossing[0]]
        prev, cur = None, crossing[0]
        while True:
            nxt = next(w for w in adj[cur] if w != prev)
            if nxt == crossing[0]:
                break
            order.append(nxt)
            prev, cur = cur, nxt
        o0, o1, o2, o3 = order
        table[case] = [(o0, o1, o2), (o0, o2, o3)]
    return table


CASE_TRIS = _case_triangles()

# Irrational fractions of a voxel, added per axis to the grid origin. Centered
# or voxel-aligned meshes (every AI export) put vertices *exactly* on grid
# nodes, where the strict inside test cannot decide — the offset makes exact
# node/edge coincidences structurally impossible without moving the surface.
GRID_OFFSET = np.array([0.6180339887498949, 0.4142135623730951, 0.3183098861837907])


def _shift(arr: np.ndarray, dx: int, dy: int, dz: int, fill) -> np.ndarray:
    """out[x, y, z] = arr[x-dx, y-dy, z-dz], padded with `fill` at the border."""
    out = np.full_like(arr, fill)
    nx, ny, nz = arr.shape
    xs, xe = max(0, dx), min(nx, nx + dx)
    ys, ye = max(0, dy), min(ny, ny + dy)
    zs, ze = max(0, dz), min(nz, nz + dz)
    if xs >= xe or ys >= ye or zs >= ze:
        return out
    out[xs:xe, ys:ye, zs:ze] = arr[xs - dx:xe - dx, ys - dy:ye - dy, zs - dz:ze - dz]
    return out


def sample_surface(vertices: np.ndarray, faces: np.ndarray,
                   spacing: float) -> tuple:
    """Barycentric lattice samples of every triangle at ~`spacing` density.

    The lattice of a triangle contains its corners, so the surface has no
    unsampled gaps. Returns (points, normals).
    """
    v0, v1, v2 = vertices[faces[:, 0]], vertices[faces[:, 1]], vertices[faces[:, 2]]
    normals = np.cross(v1 - v0, v2 - v0)
    lengths = np.linalg.norm(normals, axis=1)
    ok = lengths > 1e-14
    normals = np.where(ok[:, None], normals / np.where(ok, lengths, 1.0)[:, None],
                       np.array((0.0, 0.0, 1.0)))

    edge = np.maximum.reduce([
        np.linalg.norm(v1 - v0, axis=1),
        np.linalg.norm(v2 - v1, axis=1),
        np.linalg.norm(v0 - v2, axis=1),
    ])
    keep = ok & (edge > 0)
    n = np.maximum(1, np.ceil(np.where(keep, edge, 1.0) / spacing - 1e-12)
                   .astype(np.int64))[keep]
    tri = np.flatnonzero(keep)

    counts = (n + 1) * (n + 2) // 2          # lattice points per triangle
    total = int(counts.sum())
    if total == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))

    tri_rep = np.repeat(tri, counts)
    n_rep = np.repeat(n, counts)
    # local lattice index -> (a, b) with a + b <= n, by triangular unranking
    local = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
    a = np.minimum(np.floor((np.sqrt(8.0 * local + 1.0) - 1.0) / 2.0).astype(np.int64),
                   n_rep)
    b = local - a * (a + 1) // 2

    f0, f1, f2 = faces[tri_rep, 0], faces[tri_rep, 1], faces[tri_rep, 2]
    ta, tb = (a / n_rep)[:, None], (b / n_rep)[:, None]
    points = (vertices[f0] + ta * (vertices[f1] - vertices[f0])
              + tb * (vertices[f2] - vertices[f0]))
    return points, normals[tri_rep]


def inside_grid(tri_pts: np.ndarray, normals: np.ndarray,
                origin: np.ndarray, pitch: float, n: int) -> np.ndarray:
    """Inside/outside at every grid point by z-ray parity, shape (n+1)^3 bool.

    tri_pts: (F, 3, 3) triangle corner coordinates; normals: (F, 3) face
    normals (orientation does not matter — parity is orientation-free).
    A crossing is counted where the vertical line through a grid point's xy
    position pierces a triangle's strict xy-interior; points exactly on a
    projected edge count for no one (the irrational grid offset makes exact
    grazes essentially impossible). The xor-accumulate of crossings along z
    gives the parity: odd = inside.
    """
    eps = 1e-13                                  # above float noise, below geometry
    col_x = origin[0] + np.arange(n + 1) * pitch
    col_y = origin[1] + np.arange(n + 1) * pitch

    a2, b2, c2 = tri_pts[:, 0, :2], tri_pts[:, 1, :2], tri_pts[:, 2, :2]
    area2 = ((b2[:, 0] - a2[:, 0]) * (c2[:, 1] - a2[:, 1])
             - (b2[:, 1] - a2[:, 1]) * (c2[:, 0] - a2[:, 0]))
    ok = np.abs(area2) > 1e-18                   # z-parallel faces never cross a z-ray

    tri_min = tri_pts.min(axis=1)
    tri_max = tri_pts.max(axis=1)
    i0 = np.maximum(np.ceil((tri_min[:, 0] - origin[0]) / pitch - 1e-9).astype(np.int64), 0)
    i1 = np.minimum(np.floor((tri_max[:, 0] - origin[0]) / pitch + 1e-9).astype(np.int64), n)
    j0 = np.maximum(np.ceil((tri_min[:, 1] - origin[1]) / pitch - 1e-9).astype(np.int64), 0)
    j1 = np.minimum(np.floor((tri_max[:, 1] - origin[1]) / pitch + 1e-9).astype(np.int64), n)
    per_tri = np.where(ok, np.maximum(i1 - i0 + 1, 0) * np.maximum(j1 - j0 + 1, 0), 0)
    per_tri = per_tri.astype(np.int64)

    total = int(per_tri.sum())
    indicator = np.zeros((n + 1, n + 1, n + 1), dtype=np.int32)
    if total:
        tri_rep = np.repeat(np.flatnonzero(ok), per_tri[ok])
        starts = np.cumsum(per_tri) - per_tri
        w = np.arange(total) - np.repeat(starts, per_tri)
        cj_rep = np.repeat(np.maximum(j1 - j0 + 1, 1), per_tri)
        ii = np.repeat(i0, per_tri) + w // cj_rep
        jj = np.repeat(j0, per_tri) + w % cj_rep

        px, py = col_x[ii], col_y[jj]
        e0 = ((b2[tri_rep, 0] - a2[tri_rep, 0]) * (py - a2[tri_rep, 1])
              - (b2[tri_rep, 1] - a2[tri_rep, 1]) * (px - a2[tri_rep, 0]))
        e1 = ((c2[tri_rep, 0] - b2[tri_rep, 0]) * (py - b2[tri_rep, 1])
              - (c2[tri_rep, 1] - b2[tri_rep, 1]) * (px - b2[tri_rep, 0]))
        e2 = ((a2[tri_rep, 0] - c2[tri_rep, 0]) * (py - c2[tri_rep, 1])
              - (a2[tri_rep, 1] - c2[tri_rep, 1]) * (px - c2[tri_rep, 0]))
        # strict interior, orientation-independent: all three cross products
        # share the same sign for a strictly interior point
        pos = (e0 > eps) & (e1 > eps) & (e2 > eps)
        neg = (e0 < -eps) & (e1 < -eps) & (e2 < -eps)
        inside2 = pos | neg

        t = tri_rep[inside2]
        if len(t):
            # crossing z by 2D barycentric interpolation of the vertex z values
            # (E0/E1/E2 are the signed sub-triangle areas, already computed):
            # stable even for near-vertical faces, where a plane solve through
            # n_z would explode. E0 weighs corner c, E1 corner a, E2 corner b.
            za, zb, zc3 = tri_pts[t, 0, 2], tri_pts[t, 1, 2], tri_pts[t, 2, 2]
            zc = (e1[inside2] * za + e2[inside2] * zb + e0[inside2] * zc3) / area2[t]
            k = np.ceil((zc - origin[2]) / pitch).astype(np.int64)  # first node at/above
            valid = np.isfinite(zc) & (k >= 0) & (k <= n)
            np.add.at(indicator, (ii[inside2][valid], jj[inside2][valid], k[valid]), 1)

    return (np.cumsum(indicator, axis=2, dtype=np.int32) & 1).astype(bool)


def _flood_outside(barrier: np.ndarray, max_sweep_pairs: int = 10) -> np.ndarray:
    """True for grid nodes reachable from the grid boundary without crossing
    the barrier (shape as `barrier`, bool).

    This is the sign field for OPEN meshes: the barrier is a shell of nodes
    hugging the surface, and everything the flood cannot reach counts as
    solid. Gaps narrower than the shell thickness seal themselves — the
    pocket between two close walls is unreachable, so it becomes inside and
    the extraction bridges the crack. Sweep propagation (13 lower neighbours
    forward, 13 upper backward) reaches the same fixpoint as a BFS but is
    fully vectorised.
    """
    outside = np.zeros_like(barrier)
    outside[0, :, :] = outside[-1, :, :] = True
    outside[:, 0, :] = outside[:, -1, :] = True
    outside[:, :, 0] = outside[:, :, -1] = True
    outside &= ~barrier

    offsets = [(dx, dy, dz)
               for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
               if (dx, dy, dz) != (0, 0, 0)]
    forward = [o for o in offsets if o < (0, 0, 0)]
    backward = [o for o in offsets if o > (0, 0, 0)]
    for _ in range(max_sweep_pairs):
        before = outside.sum()
        for group in (forward, backward):
            # each step reads the state already updated by the previous step,
            # so one raster sweep chains reachability across the whole grid
            for dx, dy, dz in group:
                nb = _shift(outside, -dx, -dy, -dz, False)
                outside = (outside | nb) & ~barrier
        if outside.sum() == before:
            break
    return outside


def distance_transform(seeds: np.ndarray, sweeps: int = 4) -> np.ndarray:
    """Approximate Euclidean distance to the nearest seeded grid point.

    Multi-sweep chamfer over the 26 neighbours (forward and backward raster
    passes with true offset lengths). In the narrow band the surface
    extraction uses, the error stays well below a fifth of a voxel.
    """
    dist = np.where(seeds, np.float32(0.0), np.float32(np.inf))
    offsets = [(dx, dy, dz)
               for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
               if (dx, dy, dz) != (0, 0, 0)]
    forward = [o for o in offsets if o < (0, 0, 0)]
    backward = [o for o in offsets if o > (0, 0, 0)]
    for sweep in range(sweeps):
        groups = (forward, backward) if sweep % 2 == 0 else (backward, forward)
        for group in groups:
            for dx, dy, dz in group:
                w = np.float32(np.sqrt(dx * dx + dy * dy + dz * dz))
                # a "lower" offset (dx,dy,dz)<0 consults the neighbour one step
                # along it; an "upper" one consults the mirrored neighbour, so
                # each raster pass propagates in its own scan direction
                sx, sy, sz = (-dx, -dy, -dz) if sweep % 2 else (dx, dy, dz)
                np.minimum(dist, _shift(dist, sx, sy, sz, np.float32(np.inf)) + w,
                           out=dist)
    return dist


def marching_tetrahedra(sdf: np.ndarray) -> tuple:
    """Extract the iso-0 surface of a grid field with marching tetrahedra.

    Grid arrays are indexed sdf[x, y, z] with samples at the (n+1)^3 grid
    nodes. Every tetrahedron comes from one consistent 6-tet (Kuhn) split of
    a cell, so neighbouring tets agree on shared faces and the mesh is closed
    by construction. Returns (vertices, faces, stats) in grid units.
    """
    n = sdf.shape[0] - 1

    any_pos = np.zeros((n, n, n), dtype=bool)
    any_neg = np.zeros((n, n, n), dtype=bool)
    for c in range(8):
        dx, dy, dz = CUBE_CORNERS[c]
        vals = sdf[dx:dx + n, dy:dy + n, dz:dz + n]
        any_pos |= vals > 0
        any_neg |= vals < 0
    active = np.argwhere(any_pos & any_neg)
    stats = {"cells": int(len(active))}
    if len(active) == 0:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), stats

    ax, ay, az = active[:, 0], active[:, 1], active[:, 2]
    corner_pos = CUBE_CORNERS.astype(np.float64)

    tri_blocks = []     # (M, 3, 3) corner positions per emitted triangle
    for tet in TETS:
        offs = corner_pos[tet]                   # (4, 3) corner offsets
        vals = np.stack([sdf[ax + int(offs[k, 0]), ay + int(offs[k, 1]),
                             az + int(offs[k, 2])] for k in range(4)])
        signs = vals > 0
        case = (signs[0].astype(np.int64)
                | signs[1].astype(np.int64) << 1
                | signs[2].astype(np.int64) << 2
                | signs[3].astype(np.int64) << 3)
        live = (case > 0) & (case < 15)
        if not live.any():
            continue
        cl = case[live]
        vl = vals[:, live]                       # (4, L)

        for tri_case, tris in CASE_TRIS.items():
            m = cl == tri_case
            if not m.any():
                continue
            mm = int(m.sum())
            cell_origin = np.stack([ax[live][m], ay[live][m], az[live][m]], axis=1)
            pts = np.empty((len(tris), 3, mm, 3))
            for ti, tri in enumerate(tris):
                for ei, edge in enumerate(tri):
                    ca, cb = TET_EDGES[edge]
                    sa, sb = vl[ca][m], vl[cb][m]
                    denom = sa - sb
                    t = np.where(denom != 0, sa / np.where(denom != 0, denom, 1.0), 0.5)
                    pts[ti, ei] = (cell_origin + offs[ca]
                                   + t[:, None] * (offs[cb] - offs[ca]))
            # reorder (tri, corner, instance) -> (instance, corner) per triangle
            tri_blocks.append(np.transpose(pts, (0, 2, 1, 3)).reshape(-1, 3, 3))

    if not tri_blocks:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), stats

    corners = np.concatenate(tri_blocks)         # (F, 3, 3)
    pts = corners.reshape(-1, 3)
    # The same crossing is computed by several tets through different float
    # paths (algebraically equal, a few ulp apart), so the weld needs a
    # tolerance far above float noise and far below a voxel.
    key = np.round(pts, 6)
    uniq, inverse = np.unique(key, axis=0, return_inverse=True)
    faces = inverse.reshape(-1, 3)

    # drop triangles collapsed by the rounding weld
    good = ((faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2])
            & (faces[:, 2] != faces[:, 0]))
    faces = faces[good]

    # consistent, outward winding: topological propagation is the ground
    # truth — gradient estimates get confused by the seed-shell structure
    faces = _orient_consistently(faces, uniq)

    stats["verts"] = int(len(uniq))
    stats["faces"] = int(len(faces))
    return uniq, faces, stats


def _orient_consistently(faces: np.ndarray, vertices: np.ndarray) -> np.ndarray:
    """Flip faces so every shared edge is traversed in opposite directions,
    then choose the global orientation that makes the signed volume positive
    (normals outward). The MT surface bounds the parity-inside voxel set, so
    it is orientable and every edge has exactly two incident faces — the
    propagation is conflict-free. A single inward-pointing triangle reads as
    a hole in backface-culled viewers, which makes this pass load-bearing.
    """
    from collections import defaultdict

    edge_faces = defaultdict(list)
    for fi in range(len(faces)):
        a, b, c = faces[fi]
        for u, v in ((a, b), (b, c), (c, a)):
            edge_faces[(u, v) if u < v else (v, u)].append(fi)

    flip = np.zeros(len(faces), dtype=bool)
    seen = np.zeros(len(faces), dtype=bool)
    for start in range(len(faces)):
        if seen[start]:
            continue
        seen[start] = True
        stack = [start]
        while stack:
            fi = stack.pop()
            f = faces[fi]
            if flip[fi]:
                a, b, c = f[0], f[2], f[1]
            else:
                a, b, c = f
            for u, v in ((a, b), (b, c), (c, a)):
                key = (u, v) if u < v else (v, u)
                for nf in edge_faces[key]:
                    if nf == fi or seen[nf]:
                        continue
                    # this face (effectively) traverses the shared edge u->v,
                    # so the neighbour must traverse it v->u
                    g = faces[nf]
                    nf_l2h = any((g[i], g[(i + 1) % 3]) == key for i in range(3))
                    flip[nf] = bool(nf_l2h) != (u > v)
                    seen[nf] = True
                    stack.append(nf)

    # global orientation: outward normals enclose positive volume
    v0, v1, v2 = vertices[faces[:, 0]], vertices[faces[:, 1]], vertices[faces[:, 2]]
    volume = np.einsum('ij,ij->i', v0, np.cross(v1, v2)).sum() / 6.0
    if volume < 0:
        flip = ~flip

    out = faces.copy()
    out[flip] = out[flip][:, ::-1]
    return out


def taubin_smooth(vertices: np.ndarray, faces: np.ndarray,
                  steps: int, lam: float = 0.5, mu: float = -0.53) -> np.ndarray:
    """Taubin (lambda/mu) smoothing. Moves vertices only, so the topology —
    and with it watertightness — is untouched."""
    if steps <= 0 or len(faces) == 0:
        return vertices
    src = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2],
                          faces[:, 1], faces[:, 2], faces[:, 0]])
    dst = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0],
                          faces[:, 0], faces[:, 1], faces[:, 2]])
    k = len(vertices)
    counts = np.bincount(src, minlength=k).astype(np.float64)
    counts[counts == 0] = 1.0
    out = vertices.astype(np.float64, copy=True)
    for _ in range(steps):
        for factor in (lam, mu):
            delta = out[dst] - out[src]
            acc = np.stack([np.bincount(src, weights=delta[:, d], minlength=k)
                            for d in range(3)], axis=1)
            out += factor * acc / counts[:, None]
    return out


def project_to_surface(vertices: np.ndarray, points: np.ndarray,
                       normals: np.ndarray, origin: np.ndarray, pitch: float,
                       n: int, iters: int = 2) -> np.ndarray:
    """Slide vertices onto the sampled original surface.

    Each occupied grid cell keeps one representative sample; every vertex
    checks the representatives of its 27 surrounding cells, picks the nearest
    and projects onto that sample's tangent plane. Restores detail the
    smoothing rounded away without touching the topology.
    """
    if len(points) == 0 or iters <= 0:
        return vertices
    cx = np.clip(((points[:, 0] - origin[0]) / pitch).astype(np.int64), 0, n)
    cy = np.clip(((points[:, 1] - origin[1]) / pitch).astype(np.int64), 0, n)
    cz = np.clip(((points[:, 2] - origin[2]) / pitch).astype(np.int64), 0, n)
    flat = (cx * (n + 1) + cy) * (n + 1) + cz
    order = np.argsort(flat)
    flat_sorted = flat[order]
    unique_cells, first = np.unique(flat_sorted, return_index=True)
    rep_pts = points[order[first]]
    rep_nrm = normals[order[first]]

    out = vertices.astype(np.float64, copy=True)
    span = n + 1
    for _ in range(iters):
        gx = np.clip(np.round((out[:, 0] - origin[0]) / pitch).astype(np.int64), 0, n)
        gy = np.clip(np.round((out[:, 1] - origin[1]) / pitch).astype(np.int64), 0, n)
        gz = np.clip(np.round((out[:, 2] - origin[2]) / pitch).astype(np.int64), 0, n)
        best_d2 = np.full(len(out), np.inf)
        best_p = np.zeros_like(out)
        best_n = np.zeros_like(out)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    xx = np.clip(gx + dx, 0, n)
                    yy = np.clip(gy + dy, 0, n)
                    zz = np.clip(gz + dz, 0, n)
                    cell = (xx * span + yy) * span + zz
                    pos = np.searchsorted(unique_cells, cell)
                    pos_c = np.minimum(pos, len(unique_cells) - 1)
                    hit = unique_cells[pos_c] == cell
                    if not hit.any():
                        continue
                    idx = np.flatnonzero(hit)
                    p = rep_pts[pos_c[idx]]
                    d2 = ((out[idx] - p) ** 2).sum(axis=1)
                    upd = d2 < best_d2[idx]
                    sel = idx[upd]
                    best_d2[sel] = d2[upd]
                    best_p[sel] = p[upd]
                    best_n[sel] = rep_nrm[pos_c[idx[upd]]]
        found = np.isfinite(best_d2)
        if found.any():
            off = ((out[found] - best_p[found]) * best_n[found]).sum(axis=1, keepdims=True)
            out[found] -= off * best_n[found]
    return out


def remesh(vertices: np.ndarray, faces: np.ndarray, resolution: int,
           smoothing: float = 0.6, project_iters: int = 2,
           closed: bool = True) -> tuple:
    """The full self-contained remesh, in the caller's world coordinates.

    The mesh is normalised internally (unit diagonal box, so every threshold
    is scale-free). `closed=False` switches the sign field from exact ray
    parity to a flood fill around a thick surface shell — for fragment shells
    the hole filler could not close, where parity would leak through every
    crack. Returns (vertices, faces, stats).
    """
    vertices = np.ascontiguousarray(vertices, dtype=np.float64)
    faces = np.ascontiguousarray(faces, dtype=np.int64)
    stats = {}

    lo = vertices.min(axis=0)
    hi = vertices.max(axis=0)
    center = (lo + hi) / 2.0
    diag = float(np.linalg.norm(hi - lo))
    if diag <= 0 or len(faces) == 0:
        raise ValueError("mesh is empty or degenerate")
    verts_n = (vertices - center) / diag

    n = int(max(8, min(256, resolution)))
    margin = 2
    pitch = 1.0 / (n - 2 * margin)
    # per-axis irrational offset, see GRID_OFFSET
    origin = -0.5 - margin * pitch + pitch * GRID_OFFSET

    points, normals = sample_surface(verts_n, faces, pitch * 0.5)
    stats["samples"] = int(len(points))

    seeds = np.zeros((n + 1, n + 1, n + 1), dtype=bool)
    sx = np.clip(((points[:, 0] - origin[0]) / pitch).astype(np.int64), 0, n)
    sy = np.clip(((points[:, 1] - origin[1]) / pitch).astype(np.int64), 0, n)
    sz = np.clip(((points[:, 2] - origin[2]) / pitch).astype(np.int64), 0, n)
    seeds[sx, sy, sz] = True
    dist_raw = distance_transform(seeds)

    if closed:
        inside = inside_grid(verts_n[faces], normals, origin, pitch, n)
        stats["sign"] = "parity"
    else:
        # A half-voxel-thick shell hugging the surface blocks the flood, so
        # everything it cannot reach from the outside counts as solid —
        # cracks narrower than the shell seal themselves, and the double
        # walls of a fragmented export stop oscillating the field.
        barrier = dist_raw < np.float32(pitch * 0.5)
        inside = ~_flood_outside(barrier)
        stats["sign"] = "flood (open mesh)"

    # Seeds mark nodes *on* the surface; their exact zeros would swallow the
    # sign boundary (a node with sdf == 0 belongs to neither side, so cells
    # covered by seeds are never extracted). The sign alone decides
    # inside/outside — the distance only shapes where between two straddling
    # nodes the surface lands — so lift the field off zero for extraction.
    dist = np.maximum(dist_raw, np.float32(pitch * 0.25))
    sdf = dist * np.where(inside, np.float32(-1.0), np.float32(1.0))

    verts_g, faces_g, mstats = marching_tetrahedra(sdf)
    stats.update(mstats)
    if len(faces_g) == 0:
        raise ValueError(f"surface extraction found nothing at resolution {n} — "
                         "raise the resolution")
    if len(faces_g) < 100:
        raise ValueError(f"extraction produced only {len(faces_g)} faces at "
                         f"resolution {n} — the result would be garbage; raise "
                         "the resolution")

    verts_w = origin + verts_g * pitch           # grid units -> normalized
    steps = int(round(2 + smoothing * 18))
    verts_w = taubin_smooth(verts_w, faces_g, steps)
    verts_w = project_to_surface(verts_w, points, normals, origin, pitch, n,
                                 iters=project_iters)

    stats["resolution"] = n
    stats["smoothing_steps"] = steps
    return verts_w * diag + center, faces_g.astype(np.int64), stats
