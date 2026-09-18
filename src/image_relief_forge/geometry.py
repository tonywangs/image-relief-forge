"""Construct a closed graph surface over a rectangular, flat-bottomed tile."""

import struct

import numpy as np


def triangulate(heights: np.ndarray, width: float, depth: float) -> np.ndarray:
    """Image rows run down; model Y runs up. Return outward float32 triangles."""
    ny, nx = heights.shape
    x, y = np.meshgrid(np.linspace(0, width, nx), np.linspace(0, depth, ny))
    top = np.column_stack((x.ravel(), y.ravel(), heights[::-1].ravel()))
    bottom = top.copy()
    bottom[:, 2] = 0
    vertices = np.concatenate((top, bottom))
    n = nx * ny
    grid = np.arange(n).reshape(ny, nx)
    a = grid[:-1, :-1].ravel()
    b, c, d = a + 1, a + nx + 1, a + nx
    top_faces = np.concatenate((np.column_stack((a, b, c)), np.column_stack((a, c, d))))
    bottom_faces = top_faces[:, ::-1] + n
    # Counterclockwise boundary as seen from +Z, each corner exactly once.
    boundary = np.concatenate((grid[0, :-1], grid[:-1, -1], grid[-1, :0:-1], grid[:0:-1, 0]))
    following = np.roll(boundary, -1)
    walls = np.concatenate((np.column_stack((boundary, boundary + n, following + n)),
                            np.column_stack((boundary, following + n, following))))
    return vertices[np.concatenate((top_faces, bottom_faces, walls))].astype("<f4")


def write_stl(path, triangles: np.ndarray) -> None:
    """Fixed header and ordering, no timestamps; normals use serialized coordinates."""
    vectors = triangles.astype(np.float64)
    normals = np.cross(vectors[:, 1] - vectors[:, 0], vectors[:, 2] - vectors[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    if not np.isfinite(vectors).all() or np.any(lengths == 0):
        raise ValueError("geometry collapses at STL float32 precision")
    normals /= lengths[:, None]
    records = np.zeros(len(triangles), dtype=[("normal", "<f4", (3,)),
                                             ("vertices", "<f4", (3, 3)), ("attribute", "<u2")])
    records["normal"] = normals
    records["vertices"] = triangles
    with open(path, "wb") as stream:
        stream.write(b"Image Relief Forge 0.1; coordinates in millimeters".ljust(80, b"\0"))
        stream.write(struct.pack("<I", len(triangles)))
        stream.write(records.tobytes())


def expected_volume(heights: np.ndarray, width: float, depth: float) -> float:
    """Integral of the piecewise linear top surface, before STL quantization."""
    z = heights[::-1]
    cell_area = width * depth / ((z.shape[0] - 1) * (z.shape[1] - 1))
    total = (2 * z[:-1, :-1] + z[:-1, 1:] + 2 * z[1:, 1:] + z[1:, :-1]).sum()
    return float(total * cell_area / 6)
