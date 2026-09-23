"""Cross-check tiles with Trimesh and an independently exported untiled mesh."""

from dataclasses import replace
import hashlib
import json
import struct
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
import pytest

from image_relief_forge.assembly import validate_assembly
from image_relief_forge.cli import main
from image_relief_forge.conversion import Parameters, convert
from image_relief_forge.geometry import write_stl
from image_relief_forge.tiling import partitions
from test_conversion import FIXTURES, independent_mesh


def read_manifest(path):
    return json.loads((path / "manifest.json").read_text())


def save_manifest(path, manifest):
    (path / "manifest.json").write_text(json.dumps(manifest))


def surface(mesh, offset=(0, 0, 0)):
    triangles = mesh.vertices[mesh.faces]
    return triangles[(triangles[:, :, 2] > 0).all(axis=1)] + offset


def signatures(triangles, spacing, nx):
    ij = np.rint(triangles[:, :, :2] / spacing).astype(int)
    ids = np.sort(ij[:, :, 1] * nx + ij[:, :, 0], axis=1)
    return sorted(map(tuple, ids.tolist()))


@pytest.mark.parametrize("fixture,width,bounds", [
    ("orientation.png", 80, (31, 25)), ("gradient.png", 80, (30, 22)),
    ("gradient.jpg", 80, (30, 22)), ("black.png", 40, (17, 11)),
    ("gray.png", 40, (17, 11)), ("white.png", 40, (17, 11)),
    ("transparent.png", 80, (30, 22)), ("wide.png", 100, (40, 0.1)),
    ("tall.png", 0.1, (0.1, 40)),
])
@pytest.mark.parametrize("mode", ["relief", "lithophane"])
def test_reconstruct_against_untiled(tmp_path, fixture, width, bounds, mode):
    params = Parameters(width=width, resolution=23, mode=mode)
    plain = convert(FIXTURES / fixture, tmp_path / "plain", params)
    report = convert(FIXTURES / fixture, tmp_path / "tiled", replace(params, max_tile_width=bounds[0], max_tile_height=bounds[1]))
    bundle = tmp_path / "tiled"
    manifest = read_manifest(bundle)
    assert manifest["tile_count"] > 1
    assert report["validation"]["passed"]
    assert validate_assembly(bundle) == report["validation"]
    untiled = independent_mesh(tmp_path / "plain/model.stl")
    nx, ny = manifest["grid_vertices_xy"]
    spacing = np.asarray(manifest["dimensions_mm_xy"]) / np.array([nx - 1, ny - 1])
    all_top, volumes = [], []
    for tile in manifest["tiles"]:
        mesh = independent_mesh(bundle / tile["file"])
        assert mesh.is_watertight and mesh.is_winding_consistent
        assert mesh.euler_number == 2 and (mesh.area_faces > 0).all()
        assert mesh.volume > 0
        assert np.allclose(mesh.bounds[0], 0)
        assert np.all(mesh.extents[:2] <= np.array(bounds) + 1e-5)
        assert np.allclose(mesh.extents, tile["dimensions_mm"])
        all_top.append(surface(mesh, tile["assembly_origin_mm"]))
        volumes.append(mesh.volume)
    top = np.concatenate(all_top)
    assert signatures(top, spacing, nx) == signatures(surface(untiled), spacing, nx)
    # Actual vertex positions and heights, not just topological cell IDs.
    reference = np.unique(surface(untiled).reshape(-1, 3), axis=0)
    reference_ids = np.rint(reference[:, :2] / spacing).astype(int)
    field = {tuple(ij): xyz for ij, xyz in zip(reference_ids, reference)}
    for vertex in np.unique(top.reshape(-1, 3), axis=0):
        key = tuple(np.rint(vertex[:2] / spacing).astype(int))
        assert np.allclose(vertex, field[key], rtol=1e-6, atol=1e-7)
    assert sum(volumes) == pytest.approx(untiled.volume, rel=1e-6)
    assert sum(volumes) == pytest.approx(plain["expected_volume_mm3"], rel=1e-6)
    assert report["validation"]["shared_vertex_observations"] > 0
    assert report["validation"]["max_seam_error_mm_xyz"][2] == 0


def test_exact_orientation_and_uneven_grid(tmp_path):
    convert(FIXTURES / "orientation.png", tmp_path / "out",
            Parameters(width=100, resolution=20, max_tile_width=40, max_tile_height=30))
    manifest = read_manifest(tmp_path / "out")
    assert manifest["layout_columns_rows"] == [3, 3]
    assert manifest["tiles"][0]["grid_extent_xy"] == [[0, 0], [7, 5]]
    assert manifest["tiles"][-1]["grid_extent_xy"] == [[14, 10], [19, 11]]
    vertices = np.concatenate([surface(independent_mesh(tmp_path / "out" / t["file"]), t["assembly_origin_mm"]).reshape(-1, 3) for t in manifest["tiles"]])
    for x, y, brightness in [(0, 60, 0), (100, 60, 64), (0, 0, 192), (100, 0, 255)]:
        selected = vertices[np.isclose(vertices[:, 0], x) & np.isclose(vertices[:, 1], y)]
        assert len(selected)
        assert np.allclose(selected[:, 2], 1 + 3 * brightness / 255)


@pytest.mark.parametrize("limit,count", [(20, 4), (40, 1), (2000, 1)])
def test_exact_boundary(tmp_path, limit, count):
    source = tmp_path / "square.png"
    Image.new("L", (5, 5), 128).save(source)
    report = convert(source, tmp_path / "out", Parameters(width=40, resolution=5, max_tile_width=limit, max_tile_height=limit))
    assert report["validation"]["tile_count"] == count
    assert report["validation"]["passed"]
    assert partitions(11, 1, 0.3) == [(0, 3), (3, 6), (6, 9), (9, 10)]


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "20", 2001])
@pytest.mark.parametrize("axis", ["max_tile_width", "max_tile_height"])
def test_invalid_bounds(tmp_path, axis, value):
    params = replace(Parameters(max_tile_width=30, max_tile_height=30), **{axis: value})
    with pytest.raises(ValueError, match=axis):
        convert(FIXTURES / "gray.png", tmp_path / "out", params)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("changes,match", [
    ({"max_tile_width": 20}, "both"), ({"max_tile_height": 20}, "both"),
    ({"max_tile_width": 0.1, "max_tile_height": 30}, "one sampled cell"),
    ({"max_tile_width": 3.3, "max_tile_height": 3.3}, "limit is 256"),
])
def test_actionable_resource_errors(tmp_path, changes, match):
    with pytest.raises(ValueError, match=match):
        convert(FIXTURES / "gray.png", tmp_path / "out", replace(Parameters(resolution=32), **changes))
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".relief-forge-*"))


def test_256_tiles_and_zero_relief(tmp_path):
    source = tmp_path / "square.png"
    Image.new("L", (17, 17), 0).save(source)
    report = convert(source, tmp_path / "out", Parameters(width=16, resolution=17, relief=0, max_tile_width=1, max_tile_height=1))
    assert report["validation"]["tile_count"] == 256
    assert report["validation"]["volume_mm3"] == 256
    assert report["validation"]["triangle_count"] == 256 * 12


def test_all_artifact_bytes_repeat_and_svg_offline(tmp_path):
    for name in ("a", "b"):
        convert(FIXTURES / "gradient.jpg", tmp_path / name, Parameters(width=80, resolution=32, max_tile_width=30, max_tile_height=25))
    paths = list((tmp_path / "a").iterdir())
    assert len(paths) > 5
    for path in paths:
        assert path.read_bytes() == (tmp_path / "b" / path.name).read_bytes()
    root = ET.parse(tmp_path / "a/assembly.svg").getroot()
    images = root.findall(".//{http://www.w3.org/2000/svg}image")
    assert len(images) == 1 and images[0].attrib["href"].startswith("data:image/png;base64,")
    svg = (tmp_path / "a/assembly.svg").read_text()
    for tile in read_manifest(tmp_path / "a")["tiles"]:
        assert tile["id"] in svg
    assert "+Y up" in svg and "view from +Z" in svg


@pytest.fixture
def bundle(tmp_path):
    path = tmp_path / "bundle"
    convert(FIXTURES / "orientation.png", path, Parameters(resolution=12, max_tile_width=40, max_tile_height=30))
    return path


@pytest.mark.parametrize("mutation,failed", [
    ("gap", "complete_coverage_without_overlap"), ("origin", "surface_coordinates"),
    ("bounds", "build_bounds"), ("height", "surface_coordinates"),
    ("hash", "tile_hashes"), ("dimensions", "manifest_geometry"),
    ("overlap", "complete_coverage_without_overlap"),
])
def test_assembly_rejects_damage(bundle, mutation, failed):
    manifest = read_manifest(bundle)
    tile = manifest["tiles"][0]
    if mutation == "gap":
        manifest["tiles"].pop()
        manifest["tile_count"] -= 1
    elif mutation == "origin":
        tile["assembly_origin_mm"][0] += 0.05
    elif mutation == "bounds":
        manifest["max_tile_dimensions_mm_xy"][0] = 1
    elif mutation == "height":
        path = bundle / tile["file"]
        data = path.read_bytes()
        count = struct.unpack_from("<I", data, 80)[0]
        triangles = np.ndarray((count, 3, 3), dtype="<f4", buffer=data, offset=96, strides=(50, 12, 4)).copy()
        triangles[:, :, 2][triangles[:, :, 2] > 0] += 0.1
        write_stl(path, triangles)
        tile["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif mutation == "hash":
        tile["sha256"] = "0" * 64
    elif mutation == "dimensions":
        tile["dimensions_mm"][0] += 1
    elif mutation == "overlap":
        original = manifest["tiles"][1]
        copied = dict(tile, file=original["file"])
        (bundle / copied["file"]).write_bytes((bundle / tile["file"]).read_bytes())
        manifest["tiles"][1] = copied
    save_manifest(bundle, manifest)
    result = validate_assembly(bundle)
    assert not result["passed"]
    assert not result["checks"][failed]
    if mutation == "height":
        assert not result["checks"]["seam_profiles"]


@pytest.mark.parametrize("mutation", ["traversal", "duplicate", "grid", "npy", "version", "count", "symlink"])
def test_invalid_bundle_metadata(bundle, mutation):
    manifest = read_manifest(bundle)
    if mutation == "traversal":
        manifest["tiles"][0]["file"] = "../model.stl"
    elif mutation == "duplicate":
        manifest["tiles"][1]["file"] = manifest["tiles"][0]["file"]
    elif mutation == "grid":
        manifest["grid_vertices_xy"] = [1000000, 1000000]
    elif mutation == "npy":
        with (bundle / "surface.npy").open("wb") as f:
            np.lib.format.write_array_header_1_0(f, {"shape": (1000000, 1000000), "fortran_order": False, "descr": "<f8"})
    elif mutation == "version":
        manifest["schema_version"] = 99
    elif mutation == "count":
        manifest["tile_count"] = 257
    elif mutation == "symlink":
        path = bundle / manifest["tiles"][0]["file"]
        other = bundle / "other.stl"
        path.rename(other)
        path.symlink_to(other)
    save_manifest(bundle, manifest)
    with pytest.raises(ValueError):
        validate_assembly(bundle)


def test_cli(bundle, capsys):
    assert main(["validate-assembly", str(bundle)]) == 0
    assert json.loads(capsys.readouterr().out)["passed"]
    (bundle / "manifest.json").write_text("{}")
    assert main(["validate-assembly", str(bundle)]) == 2
    assert "invalid assembly manifest" in capsys.readouterr().err


def test_changed_diagonal_rejected_even_for_valid_solid(bundle):
    manifest = read_manifest(bundle)
    tile = manifest["tiles"][0]
    path = bundle / tile["file"]
    mesh = independent_mesh(path)
    triangles = mesh.vertices[mesh.faces].astype("<f4")
    top = triangles[(triangles[:, :, 2] > 0).all(axis=1)]
    xs, ys = np.unique(top[:, :, 0]), np.unique(top[:, :, 1])
    x0, x1, y0, y1 = xs[0], xs[1], ys[0], ys[1]
    selected = ((triangles[:, :, 2] > 0).all(axis=1) &
                (triangles[:, :, 0] <= x1).all(axis=1) &
                (triangles[:, :, 1] <= y1).all(axis=1))
    indices = np.flatnonzero(selected)
    assert len(indices) == 2
    vertices = np.unique(triangles[selected].reshape(-1, 3), axis=0)
    def point(x, y):
        return vertices[(vertices[:, 0] == x) & (vertices[:, 1] == y)][0]
    a, b, c, d = point(x0, y0), point(x1, y0), point(x1, y1), point(x0, y1)
    triangles[indices] = [[a, b, d], [b, c, d]]
    write_stl(path, triangles)
    tile["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    save_manifest(bundle, manifest)
    check = validate_assembly(bundle)
    assert check["checks"]["tile_geometry"]
    assert check["checks"]["surface_coordinates"]
    assert not check["checks"]["surface_connectivity"]
    assert not check["checks"]["complete_coverage_without_overlap"]


def test_failed_tiled_validation_leaves_no_bundle(tmp_path, monkeypatch):
    import image_relief_forge.conversion as conversion
    monkeypatch.setattr(conversion, "validate_assembly", lambda _: {"passed": False, "checks": {"seams": False}})
    with pytest.raises(ValueError, match="seams"):
        convert(FIXTURES / "gray.png", tmp_path / "out", Parameters(resolution=10, max_tile_width=40, max_tile_height=30))
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".relief-forge-*"))


@pytest.mark.parametrize("fixture,mode,sha", [
    ("gradient.png", "relief", "21d6c8baf03b0b47d7fda131225c536e791eb749c9515d38e2b9eb46d289c00d"),
    ("gradient.jpg", "lithophane", "0d4ed6a58ee741404b9aa5419d9ea11d1773081b0afcafcca42d223c5381475f"),
    ("orientation.png", "relief", "65d870acf42024c4a324f1b0c0c3329acf6be38a6b4c39b667ea28c6e78bf594"),
    ("transparent.png", "lithophane", "6fd5dccdda63f3f3a7557bb9be493e069dc5bdbf47d7dedf33627dedb827153b"),
])
def test_previous_single_mesh_bytes(tmp_path, fixture, mode, sha):
    # Historical hashes are tied to the pinned decoder/resampler environment.
    import PIL
    if PIL.__version__ != "12.3.0" or np.__version__ != "2.5.3":
        pytest.skip("historical artifact hashes require pinned NumPy/Pillow versions")
    convert(FIXTURES / fixture, tmp_path / "out", Parameters(width=80, base=0.8, relief=2.4, resolution=64, mode=mode))
    assert hashlib.sha256((tmp_path / "out/model.stl").read_bytes()).hexdigest() == sha


@pytest.mark.parametrize("mode,invert", [("relief", False), ("relief", True), ("lithophane", False), ("lithophane", True)])
def test_tiled_constant_analytic_volume(tmp_path, mode, invert):
    source = tmp_path / "constant.png"
    Image.new("L", (7, 5), 128).save(source)
    params = Parameters(width=70, base=2, relief=5, resolution=17, mode=mode, invert=invert,
                        max_tile_width=30, max_tile_height=22)
    report = convert(source, tmp_path / "out", params)
    b = 128 / 255
    thickness = 2 + 5 * (1 - b if (mode == "lithophane") != invert else b)
    assert report["validation"]["volume_mm3"] == pytest.approx(70 * 50 * thickness, rel=1e-6)
