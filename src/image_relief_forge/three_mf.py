"""Deterministic Core 3MF 1.3.0 mesh-only export; no slicer extensions."""

import hashlib
import io
import json
import re
import struct
import zipfile
from pathlib import Path

import numpy as np

MAX_MESHES = 256
MAX_VERTICES = 131_072
MAX_MESH_TRIANGLES = 262_140
MAX_TRIANGLES = 600_000
MAX_BYTES = 64 * 1024 * 1024
CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PARTS = ("[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model")
CONTENT_TYPES = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>\n'''
RELATIONSHIPS = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>\n'''


class LimitedBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_BYTES:
            raise ValueError("3MF output exceeds 64 MiB limit")
        return super().write(data)


def number(value):
    value = float(value)
    if not np.isfinite(value):
        raise ValueError("nonfinite 3MF coordinate")
    return "0" if value == 0 else format(value, ".17g")


def source_entries(directory):
    directory = Path(directory)
    if (directory / "manifest.json").exists():
        path = directory / "manifest.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("oversized manifest")
        tiles = json.loads(path.read_text())["tiles"]
        entries = [(t["id"], t["file"], t["assembly_origin_mm"]) for t in tiles]
    else:
        entries = [("model", "model.stl", [0, 0, 0])]
    return validate_entries(entries)


def validate_entries(entries):
    if not 1 <= len(entries) <= MAX_MESHES:
        raise ValueError("3MF mesh count limit exceeded")
    seen = set()
    for name, filename, origin in entries:
        if not isinstance(name, str) or not re.fullmatch(r"model|tile-r[0-9]{3}-c[0-9]{3}", name) or filename != name + ".stl" or name in seen:
            raise ValueError("invalid or duplicate 3MF mesh identity")
        seen.add(name)
        a = np.asarray(origin, dtype=float)
        if a.shape != (3,) or not np.isfinite(a).all() or np.any(np.abs(a) > 2000):
            raise ValueError("invalid assembly origin")
    return entries


def read_stl(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 84 <= path.stat().st_size <= 84 + 50 * MAX_MESH_TRIANGLES:
        raise ValueError("STL outside 3MF mesh resource limits")
    data = path.read_bytes()
    count = struct.unpack_from("<I", data, 80)[0]
    if not 1 <= count <= MAX_MESH_TRIANGLES or len(data) != 84 + 50 * count:
        raise ValueError("invalid STL size")
    triangles = np.ndarray((count, 3, 3), dtype="<f4", buffer=data, offset=96, strides=(50, 12, 4)).copy()
    if not np.isfinite(triangles).all():
        raise ValueError("nonfinite mesh")
    return triangles


def export_3mf(directory, *, entries=None, transforms=None, destination=None):
    """Export a deterministic package, optionally with explicit bed build transforms.

    Defaults preserve the assembly-positioned model.3mf API and exact bytes.
    Every destination is opened exclusively.
    """
    directory = Path(directory)
    entries = source_entries(directory) if entries is None else validate_entries(entries)
    if transforms is not None:
        transforms = np.asarray(transforms, dtype=np.float64)
        if transforms.shape != (len(entries), 4, 3) or not np.isfinite(transforms).all():
            raise ValueError("invalid 3MF transforms")
    xml = LimitedBuffer()
    def emit(line):
        xml.write((line + "\n").encode("utf-8"))
    emit('<?xml version="1.0" encoding="UTF-8"?>')
    emit(f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE}"><resources>')
    total = 0
    for index, (name, filename, origin) in enumerate(entries, 1):
        triangles = read_stl(directory / filename)
        total += len(triangles)
        if total > MAX_TRIANGLES:
            raise ValueError("3MF total triangle limit exceeded")
        vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
        if len(vertices) > MAX_VERTICES:
            raise ValueError("3MF vertex limit exceeded")
        emit(f'<object id="{index}" type="model" name="{name}" partnumber="{name}"><mesh><vertices>')
        for x, y, z in vertices:
            emit(f'<vertex x="{number(x)}" y="{number(y)}" z="{number(z)}"/>')
        emit('</vertices><triangles>')
        for a, b, c in inverse.reshape(-1, 3):
            emit(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>')
        emit('</triangles></mesh></object>')
    emit('</resources><build>')
    for index, (_, _, origin) in enumerate(entries, 1):
        transform = "1 0 0 0 1 0 0 0 1 " + " ".join(map(number, origin))
        if transforms is not None:
            transform = " ".join(map(number, transforms[index - 1].ravel()))
        emit(f'<item objectid="{index}" transform="{transform}"/>')
    emit('</build></model>')
    package = LimitedBuffer()
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name, data in zip(PARTS, (CONTENT_TYPES, RELATIONSHIPS, xml.getvalue())):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    path = directory / "model.3mf" if destination is None else Path(destination)
    try:
        with path.open("xb") as stream:
            try:
                stream.write(package.getvalue())
            except BaseException:
                path.unlink()
                raise
    except FileExistsError as exc:
        raise ValueError("3MF output already exists") from exc
    return {"file": path.name, "sha256": hashlib.sha256(package.getvalue()).hexdigest(),
            "bytes": path.stat().st_size, "core_specification": "1.3.0",
            "coordinates": "assembly positions; not a print-bed arrangement" if transforms is None else "explicit build transforms", "mesh_count": len(entries)}
