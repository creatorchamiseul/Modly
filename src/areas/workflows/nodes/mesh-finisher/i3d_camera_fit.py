"""Fit a camera to a generated mesh from a single source photo (for Pixel Match).

image-to-3dlab gets exact cameras only from Pixal3D runs (their `.svviews/` folder with a
`transforms.json`). Modly assets come from several generators, none of which stage a
camera, so this module recovers one: the photo's foreground mask is compared against the
mesh's silhouette while a small parameter set (yaw, pitch, camera distance, plus a 2D
scale/offset alignment) is searched coarse-to-fine. The camera maximising silhouette IoU
is handed to `i3d_photo_paint` as an ordinary `View`; when the best IoU is too low the
caller should skip Pixel Match rather than paint from a bad camera.

The projection mirrors `i3d_photo_paint.project()` so the same maths drives the fit and
the paint. Mesh coordinates are normalised (centred, largest side = 1) for fitting; the
View that comes back is in the mesh's own coordinates.

Vendored/adapted concept from image-to-3dlab's photo_paint tooling (Apache-2.0,
Bingeljell); the fitting search is new for Modly.
"""

from __future__ import annotations

import math

import numpy as np

from i3d_photo_paint import View, project, rasterize

RENDER = 144          # width of the fitting frame; the height follows the photo's aspect
MIN_IOU = 0.45        # below this the fit is not trusted and Pixel Match should be skipped
FOV40 = math.radians(40.0)


# ─── the photo's foreground mask ────────────────────────────────────────────────────────

def image_matte(image: np.ndarray) -> np.ndarray | None:
    """Foreground mask from an RGBA photo (its alpha), or a border-colour fallback.

    Returns None when the photo gives no usable silhouette (a fully opaque busy image
    with a flat share of "foreground", or a fully transparent one).
    """
    if image.ndim == 3 and image.shape[2] == 4 and int(image[..., 3].min()) < 240:
        return image[..., 3] > 127

    rgb = image[..., :3].astype(np.float32)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]], axis=0)
    background = np.median(border, axis=0)
    distance = np.linalg.norm(rgb - background, axis=2)
    threshold = max(20.0, float(np.percentile(distance, 75)) * 0.35)
    mask = distance > threshold
    share = float(mask.mean())
    if share < 0.01 or share > 0.95:
        return None
    return mask


def _resize_mask(mask: np.ndarray, width: int, height: int) -> np.ndarray:
    from PIL import Image

    picture = Image.fromarray((mask.astype(np.uint8) * 255)).resize(
        (width, height), Image.BILINEAR)
    return np.asarray(picture) > 127


def _moments(mask: np.ndarray):
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return float(xs.mean()), float(ys.mean()), float(xs.size)


# ─── camera construction and silhouette rendering ───────────────────────────────────────

def _look_at(direction, distance: float) -> np.ndarray:
    """cam_to_world for a camera at ``direction * distance`` looking at the origin.

    Same convention as i3d_photo_paint.project(): the camera looks down its own -Z,
    +Y is up.
    """
    position = np.asarray(direction, dtype=np.float64) * distance
    z = position / max(np.linalg.norm(position), 1e-12)   # +Z points back to the camera
    up = np.array([0.0, 1.0, 0.0])
    if abs(float(np.dot(up, z))) > 0.999:
        up = np.array([0.0, 0.0, 1.0])
    x = np.cross(up, z)
    x /= max(np.linalg.norm(x), 1e-12)
    y = np.cross(z, x)
    matrix = np.eye(4)
    matrix[:3, 0], matrix[:3, 1], matrix[:3, 2], matrix[:3, 3] = x, y, z, position
    return matrix


def _silhouette(positions: np.ndarray, faces: np.ndarray, cam: np.ndarray,
                dims: tuple[int, int], scale: float, offset: tuple[float, float]
                ) -> np.ndarray:
    """Rasterised coverage mask of the mesh in the fitting frame."""
    height, width = dims
    view = View(image=np.zeros((height, width, 4), np.uint8), cam_to_world=cam,
                fov_x=FOV40)
    x, y, depth = project(positions, view)
    x = (x - width / 2.0) * scale + width / 2.0 + offset[0]
    y = (y - height / 2.0) * scale + height / 2.0 + offset[1]
    tri_xy = np.stack([x, y], axis=1)[faces]
    face_of, _ = rasterize(tri_xy, depth[faces], (height, width))
    return face_of >= 0


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum()) / float(union) if union else 0.0


def _score(positions: np.ndarray, faces: np.ndarray, cam: np.ndarray,
           target: np.ndarray, dims: tuple[int, int]):
    """Render, align by moments, render again, return (IoU, (scale, offset))."""
    height, width = dims
    first = _silhouette(positions, faces, cam, dims, 1.0, (0.0, 0.0))
    rendered, wanted = _moments(first), _moments(target)
    if rendered is None or wanted is None:
        return 0.0, (1.0, (0.0, 0.0))
    scale = min(max(math.sqrt(max(wanted[2], 1.0) / max(rendered[2], 1.0)), 0.4), 2.5)
    offset = (
        wanted[0] - ((rendered[0] - width / 2.0) * scale + width / 2.0),
        wanted[1] - ((rendered[1] - height / 2.0) * scale + height / 2.0),
    )
    second = _silhouette(positions, faces, cam, dims, scale, offset)
    return _iou(second, target), (scale, offset)


# ─── the search ─────────────────────────────────────────────────────────────────────────

def fit_camera(positions: np.ndarray, faces: np.ndarray, image: np.ndarray):
    """Search for (yaw, pitch, distance) with the best silhouette IoU.

    Returns ``(View | None, iou, info)``. The View is built in the mesh's own
    coordinates and carries the photo (with the matte in its alpha) for the painter.
    """
    matte = image_matte(image)
    if matte is None:
        return None, 0.0, {"reason": "no usable silhouette (alpha or background)"}

    height, width = image.shape[:2]
    frame_w = RENDER
    frame_h = max(16, round(frame_w * height / width))
    dims = (frame_h, frame_w)
    target = _resize_mask(matte, frame_w, frame_h)
    wanted = _moments(target)
    if wanted is None or wanted[2] < 30:
        return None, 0.0, {"reason": "photo silhouette too small"}

    raw = np.asarray(positions, dtype=np.float64)
    centre = (raw.max(0) + raw.min(0)) / 2.0
    world_size = float(np.ptp(raw, axis=0).max())
    if not np.isfinite(world_size) or world_size <= 1e-12:
        return None, 0.0, {"reason": "degenerate mesh"}
    normalised = (raw - centre) / world_size

    step = max(1, len(faces) // 6000)
    fit_faces = np.asarray(faces, dtype=np.int64)[::step]

    def direction(yaw: float, pitch: float):
        return (math.sin(yaw) * math.cos(pitch), math.sin(pitch),
                math.cos(yaw) * math.cos(pitch))

    def evaluate(yaw: float, pitch: float, distance: float):
        cam = _look_at(direction(yaw, pitch), distance)
        return _score(normalised, fit_faces, cam, target, dims)

    best_iou, best_params, best_sim = -1.0, (0.0, 0.0, 2.1), (1.0, (0.0, 0.0))

    # Coarse: a full turn of yaw, seven pitches, one mid distance.
    coarse = []
    for yaw_deg in range(0, 360, 18):
        yaw = math.radians(yaw_deg)
        for pitch_deg in (-36, -24, -12, 0, 12, 24, 36):
            iou, sim = evaluate(yaw, math.radians(pitch_deg), 2.1)
            coarse.append((iou, yaw, math.radians(pitch_deg)))
    coarse.sort(key=lambda row: row[0], reverse=True)

    # Distance pass on the strongest orientations.
    for iou0, yaw, pitch in coarse[:5]:
        for distance in (1.5, 2.1, 3.0):
            iou, sim = evaluate(yaw, pitch, distance)
            if iou > best_iou:
                best_iou, best_params, best_sim = iou, (yaw, pitch, distance), sim

    # Refine: local steps around the best, twice.
    for step_deg, distance_step in ((4.0, 0.25), (1.5, 0.1)):
        y0, p0, d0 = best_params
        base = best_iou
        for dy in (-2, -1, 0, 1, 2):
            for dp in (-2, -1, 0, 1, 2):
                for dd in (-distance_step, 0.0, distance_step):
                    distance = d0 + dd
                    if distance <= 0.6:
                        continue
                    yaw = y0 + math.radians(step_deg) * dy
                    pitch = p0 + math.radians(step_deg) * dp
                    iou, sim = evaluate(yaw, pitch, distance)
                    if iou > best_iou:
                        best_iou, best_params, best_sim = iou, (yaw, pitch, distance), sim
        if best_iou <= base:
            break

    yaw, pitch, distance = best_params
    sim_scale, sim_offset = best_sim

    # ── back to the mesh's own coordinates ─────────────────────────────────────────
    # The fit was done on the normalised mesh in a `frame_w x frame_h` render; k maps
    # render pixels to photo pixels (both axes, uniform). The 2D similarity becomes the
    # focal length (scale) plus a lateral camera shift (offset).
    k = width / frame_w
    focal_render = (frame_w / 2.0) / math.tan(FOV40 / 2.0)
    focal_photo = k * focal_render * sim_scale
    fov_x = 2.0 * math.atan((width / 2.0) / focal_photo)

    axes = _look_at(direction(yaw, pitch), 1.0)[:3, :3]
    distance_world = distance * world_size
    offset_px = (k * sim_offset[0], k * sim_offset[1])
    shift_world = (
        (offset_px[0] / focal_photo) * distance_world,
        (offset_px[1] / focal_photo) * distance_world,
    )
    shift_world = (max(-0.5, min(0.5, shift_world[0] / world_size)) * world_size,
                   max(-0.5, min(0.5, shift_world[1] / world_size)) * world_size)

    position = (centre + np.asarray(direction(yaw, pitch)) * distance_world
                + axes[:, 0] * shift_world[0] + axes[:, 1] * shift_world[1])
    cam = np.eye(4)
    cam[:3, :3] = axes
    cam[:3, 3] = position

    rgb = image[..., :3]
    alpha = (matte.astype(np.uint8) * 255)[..., None]
    view_image = np.concatenate([rgb, alpha], axis=2)

    info = {
        "yaw_deg": round(math.degrees(yaw), 1),
        "pitch_deg": round(math.degrees(pitch), 1),
        "distance": round(distance, 3),
        "fov_deg": round(math.degrees(fov_x), 1),
        "render": [frame_w, frame_h],
    }
    return View(view_image, cam, fov_x), best_iou, info
