"""Read-only assembly verification; independent of partitioning and triangulation.

The reference is the saved global sample field. Actual STL top triangles are
mapped back to global grid vertices, with coordinate errors checked before any
integer indexing. No vertex coordinates or triangle connectivity are repaired.
"""

import hashlib
import json
import math
import re
import struct
from pathlib import Path

import numpy as np

from .validation import validate_stl

COORD_RTOL = 1e-6
COORD_ATOL = 1e-7
VOLUME_RTOL = 1e-6
VOLUME_ATOL = 1e-9
MAX_TOTAL_TRIANGLES = 600_000


def _array(value, shape, name):
    try:
        result = np.asarray(value, dtype=np.float64)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid manifest {name}") from exc
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f"invalid manifest {name}")
    return result


def _file(bundle, name, limit):
    path = bundle / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"missing, symlinked or oversized bundle file: {name}")
    return path


def validate_assembly(directory):
    """Recheck a tiled bundle, ignoring any previously recorded validation result."""
    try:
        return _validate_assembly(Path(directory))
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        raise ValueError(f"invalid assembly manifest: {exc}") from exc


def _validate_assembly(bundle):
    manifest = json.loads(_file(bundle, "manifest.json", 2 * 1024 * 1024).read_text(encoding="utf-8"))
    if manifest["schema_version"] != 1 or manifest["units"] != "mm":
        raise ValueError("unsupported assembly schema or units")
    grid = _array(manifest["grid_vertices_xy"], (2,), "grid")
    if np.any(grid != np.floor(grid)) or np.any(grid < 2) or np.any(grid > 256):
        raise ValueError("assembly grid must have 2..256 vertices per axis")
    nx, ny = grid.astype(int)
    dimensions = _array(manifest["dimensions_mm_xy"], (2,), "dimensions")
    limits = _array(manifest["max_tile_dimensions_mm_xy"], (2,), "build bounds")
    if np.any(dimensions < 0.1) or np.any(dimensions > 2000) or np.any(limits <= 0) or np.any(limits > 2000):
        raise ValueError("assembly dimensions or build bounds outside supported range")
    tiles = manifest["tiles"]
    if not isinstance(tiles, list) or not 1 <= len(tiles) <= 256 or manifest["tile_count"] != len(tiles):
        raise ValueError("assembly requires 1..256 tiles matching tile_count")
    source = _file(bundle, "surface.npy", 256 * 256 * 8 + 1024)
    # Reject misleading headers before NumPy allocates the declared array size.
    with source.open("rb") as stream:
        if np.lib.format.read_magic(stream) != (1, 0):
            raise ValueError("surface must use NPY version 1.0")
        shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream)
        if shape != (ny, nx) or fortran or dtype.str != "<f8" or source.stat().st_size != stream.tell() + nx * ny * 8:
            raise ValueError("surface must be a bounded C-order float64 global grid")
        heights = np.frombuffer(stream.read(), dtype="<f8").reshape(ny, nx)
    if not np.isfinite(heights).all() or heights.min() < 0.01 or heights.max() > 200:
        raise ValueError("invalid global surface heights")
    z = heights[::-1]
    width, depth = dimensions
    spacing = dimensions / (grid - 1)
    coord_tolerance = COORD_ATOL + COORD_RTOL * np.array([width, depth, float(z.max())])
    # Independently integrate two triangles per cell using the arithmetic mean
    # of their three heights and half a cell's area.
    reference_volume = float(((z[:-1, :-1] + z[:-1, 1:] + z[1:, 1:]) / 3 +
                              (z[:-1, :-1] + z[1:, 1:] + z[1:, :-1]) / 3).sum() * np.prod(spacing) / 2)
    checks = {"surface_hash": hashlib.sha256(source.read_bytes()).hexdigest() == manifest["surface"]["sha256"],
              "tile_hashes": True, "tile_geometry": True, "build_bounds": True,
              "manifest_geometry": True, "surface_coordinates": True, "surface_connectivity": True,
              "seam_profiles": True}
    cell_coverage = np.zeros((ny - 1, nx - 1), dtype=np.int32)
    triangle_coverage = np.zeros((ny - 1, nx - 1, 2), dtype=np.int32)
    seen = np.zeros((ny, nx), dtype=bool)
    first = np.zeros((ny, nx, 3), dtype=np.float64)
    max_error = np.zeros(3)
    seam_error = np.zeros(3)
    seam_samples = 0
    total_volume, total_triangles = 0.0, 0
    results, filenames = [], set()
    for tile in tiles:
        filename = tile["file"]
        if not isinstance(filename, str) or not re.fullmatch(r"tile-r[0-9]{3}-c[0-9]{3}\.stl", filename) or filename in filenames:
            raise ValueError("tile filenames must be unique tile-rNNN-cNNN.stl names")
        filenames.add(filename)
        extent = _array(tile["grid_extent_xy"], (2, 2), "tile grid extent")
        if np.any(extent != np.floor(extent)) or np.any(extent[0] < 0) or np.any(extent[1] >= grid) or np.any(extent[1] <= extent[0]):
            raise ValueError("tile grid extent must contain positive whole-cell spans inside the grid")
        (x0, y0), (x1, y1) = extent.astype(int)
        origin = _array(tile["assembly_origin_mm"], (3,), "tile origin")
        size = _array(tile["dimensions_mm"], (3,), "tile dimensions")
        expected_size = np.r_[(extent[1] - extent[0]) * spacing, z[y0:y1 + 1, x0:x1 + 1].max()]
        checks["manifest_geometry"] &= bool(np.allclose(origin, np.r_[extent[0] * spacing, 0], rtol=COORD_RTOL, atol=COORD_ATOL) and
                                             np.allclose(size, expected_size, rtol=COORD_RTOL, atol=COORD_ATOL))
        cell_coverage[y0:y1, x0:x1] += 1
        path = _file(bundle, filename, 84 + 50 * 262_140)
        # Bound total work even if a malicious manifest references many large STLs.
        with path.open("rb") as stream:
            header = stream.read(84)
        if len(header) != 84:
            raise ValueError("truncated STL")
        count = struct.unpack_from("<I", header, 80)[0]
        total_triangles += count
        if total_triangles > MAX_TOTAL_TRIANGLES:
            raise ValueError("assembly exceeds 600000 triangle resource limit")
        validation = validate_stl(path, expected_bounds=[[0, 0, 0], expected_size.tolist()])
        checks["tile_geometry"] &= validation["passed"]
        checks["build_bounds"] &= bool(np.all(np.asarray(validation["dimensions_mm"][:2]) <= limits + COORD_ATOL + COORD_RTOL * limits))
        checks["tile_hashes"] &= hashlib.sha256(path.read_bytes()).hexdigest() == tile["sha256"]
        total_volume += validation["volume_mm3"]
        results.append({"file": filename, **validation})
        data = path.read_bytes()
        triangles = np.ndarray((count, 3, 3), dtype="<f4", buffer=data, offset=96, strides=(50, 12, 4)).astype(np.float64)
        # Every top face has all vertices above the strictly positive base.
        top = triangles[np.all(triangles[:, :, 2] > 0, axis=1)] + origin
        vertices = np.unique(top.reshape(-1, 3), axis=0)
        if not len(vertices):
            checks["surface_coordinates"] = checks["surface_connectivity"] = False
            continue
        indices = np.rint(vertices[:, :2] / spacing).astype(np.int64)
        inside = np.all((indices >= [x0, y0]) & (indices <= [x1, y1]), axis=1)
        checks["surface_coordinates"] &= bool(inside.all())
        if not inside.all():
            continue
        ix, iy = indices.T
        reference = np.column_stack((ix * spacing[0], iy * spacing[1], z[iy, ix]))
        errors = np.abs(vertices - reference)
        max_error = np.maximum(max_error, errors.max(axis=0))
        checks["surface_coordinates"] &= bool(np.all(errors <= coord_tolerance))
        repeated = seen[iy, ix]
        seam_samples += int(repeated.sum())
        if repeated.any():
            differences = np.abs(vertices[repeated] - first[iy[repeated], ix[repeated]])
            seam_error = np.maximum(seam_error, differences.max(axis=0))
            checks["seam_profiles"] &= bool(np.all(differences <= coord_tolerance))
        first[iy[~repeated], ix[~repeated]] = vertices[~repeated]
        seen[iy, ix] = True
        # Verify every actual top face is one of the two specified cell triangles.
        # Sorted IDs make this independent of writer face ordering; winding was
        # checked separately using directed edges and stored normals.
        ij = np.rint(top[:, :, :2] / spacing).astype(np.int64)
        face_ids = np.sort(ij[:, :, 1] * nx + ij[:, :, 0], axis=1)
        a = face_ids[:, 0]
        cy, cx = np.divmod(a, nx)
        kind0 = (face_ids[:, 1] == a + 1) & (face_ids[:, 2] == a + nx + 1)
        kind1 = (face_ids[:, 1] == a + nx) & (face_ids[:, 2] == a + nx + 1)
        valid = (kind0 | kind1) & (cx >= x0) & (cx < x1) & (cy >= y0) & (cy < y1)
        checks["surface_connectivity"] &= bool(valid.all())
        np.add.at(triangle_coverage, (cy[valid], cx[valid], kind1[valid].astype(int)), 1)
    checks["complete_coverage_without_overlap"] = bool(np.all(cell_coverage == 1) and np.all(triangle_coverage == 1) and seen.all())
    checks["untiled_volume"] = bool(math.isclose(total_volume, reference_volume, rel_tol=VOLUME_RTOL, abs_tol=VOLUME_ATOL))
    return {"passed": all(checks.values()), "checks": {k: bool(v) for k, v in checks.items()},
            "triangle_count": total_triangles, "tile_count": len(tiles), "volume_mm3": total_volume,
            "reference_volume_mm3": reference_volume, "volume_error_mm3": abs(total_volume - reference_volume),
            "max_surface_error_mm_xyz": max_error.tolist(), "max_seam_error_mm_xyz": seam_error.tolist(),
            "shared_vertex_observations": seam_samples,
            "tolerances": {"coordinate_atol_mm": COORD_ATOL, "coordinate_rtol": COORD_RTOL,
                           "surface_seam_atol_mm_xyz": coord_tolerance.tolist(),
                           "volume_atol_mm3": VOLUME_ATOL, "volume_rtol": VOLUME_RTOL},
            "tiles": results,
            "scope": "serialized topology, build bounds, seam samples, cell coverage, surface triangles and volume; no repairs; no physical fit or optical claim"}
