"""Partition one sampled surface on grid lines; never resample individual tiles."""

import base64
import hashlib
import json
import math

import numpy as np

from .geometry import triangulate, write_stl

MAX_TILES = 256


def partitions(vertices, length, limit):
    """Greedy whole-cell spans from the low coordinate, with a final remainder."""
    spacing = length / (vertices - 1)
    # Only absorb float64 arithmetic noise, not the larger STL validation tolerance.
    cells = min(vertices - 1, math.floor(limit / spacing + 1e-12))
    if cells < 1:
        raise ValueError(f"tile bound {limit:g} mm is smaller than one sampled cell "
                         f"({spacing:g} mm); increase --resolution, reduce --width, "
                         "or increase the tile bound")
    edges = list(range(0, vertices - 1, cells)) + [vertices - 1]
    return list(zip(edges, edges[1:]))


def export_tiles(stage, heights, width, depth, max_width, max_height):
    ny, nx = heights.shape
    xs = partitions(nx, width, max_width)
    ys = partitions(ny, depth, max_height)
    if len(xs) * len(ys) > MAX_TILES:
        raise ValueError(f"partition requires {len(xs) * len(ys)} tiles; limit is {MAX_TILES}; "
                         "increase tile bounds or reduce --width")
    tiles = []
    for row, (y0, y1) in enumerate(ys, 1):
        for column, (x0, x1) in enumerate(xs, 1):
            name = f"tile-r{row:03d}-c{column:03d}"
            w, h = width * (x1 - x0) / (nx - 1), depth * (y1 - y0) / (ny - 1)
            field = heights[ny - 1 - y1:ny - y0, x0:x1 + 1]
            path = stage / f"{name}.stl"
            write_stl(path, triangulate(field, w, h))
            tiles.append({"id": name, "file": path.name, "row": row, "column": column,
                          "grid_extent_xy": [[x0, y0], [x1, y1]],
                          "assembly_origin_mm": [width * x0 / (nx - 1), depth * y0 / (ny - 1), 0.0],
                          "dimensions_mm": [w, h, float(field.max())],
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    # NPY has no timestamps; explicitly little-endian float64, image row order.
    np.save(stage / "surface.npy", heights.astype("<f8"), allow_pickle=False)
    manifest = {
        "schema_version": 1, "units": "mm", "grid_vertices_xy": [nx, ny],
        "dimensions_mm_xy": [width, depth], "max_tile_dimensions_mm_xy": [max_width, max_height],
        "tile_count": len(tiles), "layout_columns_rows": [len(xs), len(ys)],
        "partition": "whole grid cells, greedy from low X/Y; remainder at high X/Y",
        "orientation": "view from +Z: +X right, +Y up; image top at +Y; rows numbered from bottom",
        "grid_extent_semantics": "inclusive vertices; cell interiors [low, high); X/Y increase in assembly coordinates",
        "local_coordinates": "bottom at Z=0; lower-left at X=Y=0; no rotation; translate by assembly_origin_mm",
        "surface": {"file": "surface.npy", "dtype": "<f8", "row_order": "image top to bottom",
                    "sha256": hashlib.sha256((stage / "surface.npy").read_bytes()).hexdigest()},
        "tiles": tiles,
    }
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    write_map(stage, manifest)
    return manifest


def write_map(stage, manifest):
    """Self-contained SVG with embedded overall preview and a legible tile key."""
    width, height = manifest["dimensions_mm_xy"]
    scale = 620 / max(width, height)
    w, h = width * scale, height * scale
    key_top = 155 + max(h, 30)
    canvas_h = key_top + 26 * len(manifest["tiles"]) + 40
    preview = base64.b64encode((stage / "height.png").read_bytes()).decode("ascii")
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{canvas_h:g}" viewBox="0 0 900 {canvas_h:g}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<g font-family="sans-serif" fill="#162332">',
             '<text x="30" y="28" font-size="22">Relief assembly map — view from +Z</text>',
             '<text x="30" y="53" font-size="14">+X right · +Y up · image top at +Y · Z thickness out of page</text>',
             '<text x="30" y="75" font-size="14">Translate only; keep every tile in this orientation. All dimensions in mm.</text>',
             f'<text x="30" y="97" font-size="13">{manifest["layout_columns_rows"][0]} columns × {manifest["layout_columns_rows"][1]} rows; numbered left to right, bottom to top. Narrow tiles use the key below.</text>',
             f'<image x="30" y="125" width="{w:.9f}" height="{h:.9f}" preserveAspectRatio="none" href="data:image/png;base64,{preview}"/>']
    for index, tile in enumerate(manifest["tiles"], 1):
        x, y, _ = tile["assembly_origin_mm"]
        tw, th, _ = tile["dimensions_mm"]
        sx, sy = 30 + x * scale, 125 + (height - y - th) * scale
        lines.append(f'<rect x="{sx:.9f}" y="{sy:.9f}" width="{tw * scale:.9f}" height="{th * scale:.9f}" fill="none" stroke="#e34c26" stroke-width="1"/>')
        # For thin slivers the key remains readable; grid extents disambiguate.
        if tw * scale >= 20 and th * scale >= 16:
            lines.append(f'<text x="{sx + tw * scale / 2:.9f}" y="{sy + th * scale / 2:.9f}" text-anchor="middle" dominant-baseline="middle" font-size="12" fill="black" stroke="white" stroke-width="2" paint-order="stroke">{index}</text>')
        x0y0, x1y1 = tile["grid_extent_xy"]
        lines.append(f'<text x="30" y="{key_top + index * 26:g}" font-size="13">{index}: {tile["id"]} · origin ({x:.6g}, {y:.6g}) · size {tw:.6g} × {th:.6g} · grid {x0y0} to {x1y1}</text>')
    lines.append('</g></svg>\n')
    (stage / "assembly.svg").write_text("\n".join(lines), encoding="utf-8")
