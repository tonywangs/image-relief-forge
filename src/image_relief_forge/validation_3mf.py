"""Read generated bundles with consortium lib3mf, without repair operations.

This deliberately validates our bounded mesh-only profile, not arbitrary 3MF.
"""

import hashlib
import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np

from . import three_mf as fmt
from .assembly import _file, validate_assembly
from .geometry import write_stl
from .validation import validate_stl


def preflight(path, *, layout=False):
    """Bound native parser inputs before handing them to lib3mf."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > fmt.MAX_BYTES:
        raise ValueError("3MF package exceeds resource limits or is missing")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if [i.filename for i in infos] != list(fmt.PARTS) or sum(i.file_size for i in infos) > fmt.MAX_BYTES:
                raise ValueError("unsupported 3MF package parts or inflated size")
            if any(i.compress_type != zipfile.ZIP_STORED or i.flag_bits & 1 for i in infos):
                raise ValueError("expected unencrypted stored ZIP profile")
            if archive.read(fmt.PARTS[0]) != fmt.CONTENT_TYPES or archive.read(fmt.PARTS[1]) != fmt.RELATIONSHIPS:
                raise ValueError("unsupported 3MF content types or relationships")
            xml = archive.read(fmt.PARTS[2])
        if b"<!" in xml or len(xml) > fmt.MAX_BYTES:
            raise ValueError("unsupported XML declarations")
        root = ET.fromstring(xml)
    except (zipfile.BadZipFile, ET.ParseError, RuntimeError) as exc:
        raise ValueError(f"invalid 3MF package: {exc}") from exc
    q = lambda name: f"{{{fmt.CORE}}}{name}"
    if root.tag != q("model") or set(root.attrib) != {"unit", "{http://www.w3.org/XML/1998/namespace}lang"} or root.get("unit") != "millimeter" or [c.tag for c in root] != [q("resources"), q("build")]:
        raise ValueError("expected millimeter mesh-only model")
    objects, items = list(root[0]), list(root[1])
    if not 1 <= len(objects) <= fmt.MAX_MESHES or len(objects) != len(items):
        raise ValueError("invalid 3MF object/build count")
    total = 0
    for index, (obj, item) in enumerate(zip(objects, items), 1):
        if obj.tag != q("object") or set(obj.attrib) != {"id", "type", "name", "partnumber"} or obj.get("id") != str(index) or obj.get("type") != "model" or len(obj) != 1 or obj[0].tag != q("mesh"):
            raise ValueError("unsupported object profile")
        mesh = obj[0]
        if [c.tag for c in mesh] != [q("vertices"), q("triangles")]:
            raise ValueError("unsupported mesh profile")
        nv, nt = len(mesh[0]), len(mesh[1])
        total += nt
        if not 1 <= nv <= fmt.MAX_VERTICES or not 1 <= nt <= fmt.MAX_MESH_TRIANGLES or total > fmt.MAX_TRIANGLES:
            raise ValueError("3MF mesh resource limits exceeded")
        for vertex in mesh[0]:
            if vertex.tag != q("vertex") or len(vertex) or set(vertex.attrib) != {"x", "y", "z"}:
                raise ValueError("invalid vertex profile")
            xyz = np.array([float(vertex.get(k)) for k in ("x", "y", "z")])
            if not np.isfinite(xyz).all() or np.any(np.abs(xyz) > 2000):
                raise ValueError("invalid vertex coordinates")
        for face in mesh[1]:
            if face.tag != q("triangle") or len(face) or set(face.attrib) != {"v1", "v2", "v3"}:
                raise ValueError("invalid triangle profile")
            if any(not 0 <= int(face.get(k)) < nv for k in ("v1", "v2", "v3")):
                raise ValueError("invalid vertex index")
        if item.tag != q("item") or set(item.attrib) != {"objectid", "transform"} or item.get("objectid") != str(index) or len(item):
            raise ValueError("unsupported build reference")
        transform = np.array([float(x) for x in item.get("transform", "").split()])
        rotation_ok = np.array_equal(transform[:9], np.eye(3).ravel()) or (
            layout and np.array_equal(transform[:9], [0, 1, 0, -1, 0, 0, 0, 0, 1]))
        if transform.shape != (12,) or not np.isfinite(transform).all() or not rotation_ok or np.any(np.abs(transform[9:]) > 2000):
            raise ValueError("expected bounded quarter-turn bed transform" if layout else
                             "expected bounded translation-only assembly transform")


def validate_3mf(directory):
    try:
        return _validate(Path(directory))
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        raise ValueError(f"invalid 3MF bundle: {exc}") from exc


def _validate(directory):
    try:
        import lib3mf
    except ImportError as exc:
        raise ValueError("3MF validation requires the three-mf extra (lib3mf==2.5.0)") from exc
    path = directory / "model.3mf"
    preflight(path)
    entries = fmt.source_entries(directory)
    wrapper = lib3mf.get_wrapper()
    model = wrapper.CreateModel()
    reader = model.QueryReader("3mf")
    reader.SetStrictModeActive(True)
    try:
        reader.ReadFromFile(str(path.resolve()))
    except lib3mf.ELib3MFException as exc:
        raise ValueError(f"lib3mf rejected package: {exc}") from exc
    checks = {"strict_reader_without_warnings": reader.GetWarningCount() == 0,
              "millimeter_units": model.GetUnit() == lib3mf.ModelUnit.MilliMeter,
              "object_count": model.GetObjects().Count() == len(entries),
              "mesh_count": model.GetMeshObjects().Count() == len(entries),
              "build_count": model.GetBuildItems().Count() == len(entries),
              "identities": True, "transforms": True, "source_triangles": True,
              "lib3mf_manifold_oriented": True, "mesh_geometry": True}
    items = model.GetBuildItems()
    results = []
    with tempfile.TemporaryDirectory(prefix="relief-forge-3mf-check-") as temporary:
        reconstructed = Path(temporary)
        tiled = (directory / "manifest.json").exists()
        manifest = json.loads((directory / "manifest.json").read_text()) if tiled else None
        for index, (name, filename, origin) in enumerate(entries, 1):
            if not items.MoveNext():
                checks["build_count"] = False
                break
            item = items.GetCurrent()
            obj = item.GetObjectResource()
            if not obj.IsMeshObject():
                raise ValueError("3MF build item is not a mesh")
            mesh = model.GetMeshObjectByID(item.GetObjectResourceID())
            checks["identities"] &= mesh.GetResourceID() == index and mesh.GetName() == name and mesh.GetPartNumber() == name
            transform = np.array([list(row) for row in item.GetObjectTransform().Fields], dtype=np.float64)
            checks["transforms"] &= bool(np.array_equal(transform[:3], np.eye(3)) and np.allclose(transform[3], origin, rtol=1e-6, atol=1e-7))
            vertices = np.array([list(v.Coordinates) for v in mesh.GetVertices()], dtype=np.float64)
            faces = np.array([list(t.Indices) for t in mesh.GetTriangleIndices()], dtype=np.int64)
            triangles = vertices[faces]
            source = fmt.read_stl(directory / filename)
            # Float32 is the common geometry contract of STL and the lib3mf API.
            checks["source_triangles"] &= triangles.shape == source.shape and bool(np.array_equal(triangles, source))
            checks["lib3mf_manifold_oriented"] &= mesh.IsManifoldAndOriented()
            write_stl(reconstructed / filename, triangles)
            reference = validate_stl(directory / filename)
            actual = validate_stl(reconstructed / filename, expected_bounds=reference["bounds_mm"], expected_volume=reference["volume_mm3"])
            checks["mesh_geometry"] &= actual["passed"] and reference["passed"]
            results.append({"name": name, "transform": transform.tolist(), "triangles": len(faces),
                            "bounds_mm": actual["bounds_mm"], "volume_mm3": actual["volume_mm3"]})
            if tiled:
                # Use translations read by lib3mf, not the manifest's positions.
                manifest["tiles"][index - 1]["assembly_origin_mm"] = transform[3].tolist()
                manifest["tiles"][index - 1]["sha256"] = hashlib.sha256((reconstructed / filename).read_bytes()).hexdigest()
        assembly = None
        if tiled and len(results) == len(entries):
            shutil.copyfile(_file(directory, "surface.npy", 256 * 256 * 8 + 1024), reconstructed / "surface.npy")
            (reconstructed / "manifest.json").write_text(json.dumps(manifest))
            assembly = validate_assembly(reconstructed)
            checks["reconstructed_assembly"] = assembly["passed"]
    return {"passed": all(checks.values()), "checks": {k: bool(v) for k, v in checks.items()},
            "lib3mf_version": list(wrapper.GetLibraryVersion()), "meshes": results,
            "assembly": None if assembly is None else {k: v for k, v in assembly.items() if k != "tiles"},
            "scope": "strict lib3mf read, no repairs; geometry agreement and assembly reconstruction; no slicer or physical validation"}
