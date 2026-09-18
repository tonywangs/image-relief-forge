"""Reload binary STL independently of mesh construction. Never repair geometry."""

import math
import struct
from pathlib import Path

import numpy as np

MAX_TRIANGLES = 262_140  # closed 256 x 256 grid


def validate_stl(path, *, expected_bounds=None, expected_volume=None) -> dict:
    path = Path(path)
    size = path.stat().st_size
    if size < 84 or size > 84 + 50 * MAX_TRIANGLES:
        raise ValueError("STL size outside supported resource limits")
    with path.open("rb") as stream:
        header = stream.read(84)
        count = struct.unpack_from("<I", header, 80)[0]
        if not 1 <= count <= MAX_TRIANGLES or size != 84 + 50 * count:
            raise ValueError("invalid binary STL triangle count or byte length")
        # Explicit offsets keep parsing independent from the writer's record dtype.
        payload = stream.read()
    triangles = np.ndarray((count, 3, 3), dtype="<f4", buffer=payload,
                           offset=12, strides=(50, 12, 4)).astype(np.float64)
    stored_normals = np.ndarray((count, 3), dtype="<f4", buffer=payload,
                               strides=(50, 4)).astype(np.float64)
    if not np.isfinite(triangles).all() or not np.isfinite(stored_normals).all():
        raise ValueError("STL contains nonfinite coordinates or normals")
    # STL repeats coordinates per face. Exact deduplication creates an index;
    # no rounding, tolerance welding, face deletion, normal fixing or hole filling.
    vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    faces = inverse.reshape(-1, 3)
    directed = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    edges, edge_inverse, counts = np.unique(np.sort(directed, axis=1), axis=0,
                                            return_inverse=True, return_counts=True)
    signs = np.where(directed[:, 0] < directed[:, 1], 1, -1)
    balances = np.bincount(edge_inverse, weights=signs, minlength=len(edges))
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    double_areas = np.linalg.norm(cross, axis=1)
    degenerate_count = int(np.count_nonzero(double_areas == 0))
    unit = np.divide(cross, double_areas[:, None], out=np.zeros_like(cross),
                     where=double_areas[:, None] != 0)
    volume = float(np.einsum("ij,ij->i", triangles[:, 0],
                            np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6)
    bounds = np.array([vertices.min(axis=0), vertices.max(axis=0)])
    unique_faces = np.unique(np.sort(faces, axis=1), axis=0)
    checks = {
        "finite": True,
        "watertight": bool(np.all(counts == 2)),
        "consistent_winding": bool(np.all(counts == 2) and np.all(balances == 0)),
        "positive_volume": bool(math.isfinite(volume) and volume > 0),
        "no_degenerate_triangles": degenerate_count == 0,
        "no_duplicate_faces": len(unique_faces) == count,
        "stored_normals_match": bool(np.allclose(unit, stored_normals, rtol=1e-6, atol=1e-6)),
        "euler_characteristic_two": len(vertices) - len(edges) + count == 2,
    }
    if expected_bounds is not None:
        checks["expected_bounds"] = bool(np.allclose(bounds, expected_bounds, rtol=1e-6, atol=1e-7))
    if expected_volume is not None:
        checks["expected_volume"] = bool(np.isclose(volume, expected_volume, rtol=1e-6, atol=1e-9))
    return {"passed": all(checks.values()), "checks": checks, "triangle_count": count,
            "vertex_count": len(vertices), "edge_count": len(edges),
            "degenerate_triangle_count": degenerate_count, "bounds_mm": bounds.tolist(),
            "dimensions_mm": (bounds[1] - bounds[0]).tolist(), "volume_mm3": volume,
            "indexing": "exact coordinate deduplication only; no repairs",
            "scope": "topology, winding, normals, area, volume and bounds; not a general self-intersection or printability test"}
