import hashlib
import json
import struct
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
import trimesh

from image_relief_forge.cli import main
from image_relief_forge.conversion import Parameters, convert
from image_relief_forge.validation import validate_stl

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def independent_mesh(path):
    # Disable trimesh processing (which normally rounds/welds/cleans geometry).
    soup = trimesh.load_mesh(path, file_type="stl", process=False)
    vertices, inverse = np.unique(np.asarray(soup.vertices), axis=0, return_inverse=True)
    return trimesh.Trimesh(vertices=vertices, faces=inverse[soup.faces], process=False)


@pytest.mark.parametrize("fixture,width", [("black.png", 100), ("gray.png", 100),
    ("white.png", 100), ("gradient.png", 100), ("gradient.jpg", 100),
    ("orientation.png", 100), ("transparent.png", 100), ("wide.png", 100), ("tall.png", 0.1)])
@pytest.mark.parametrize("mode", ["relief", "lithophane"])
def test_independent_reload(tmp_path, fixture, width, mode):
    output = tmp_path / "bundle"
    report = convert(FIXTURES / fixture, output, Parameters(width=width, resolution=32, mode=mode))
    mesh = independent_mesh(output / "model.stl")
    assert mesh.is_watertight
    assert mesh.is_winding_consistent
    assert mesh.euler_number == 2
    assert mesh.volume > 0
    assert (mesh.area_faces > 0).all()
    assert np.allclose(mesh.bounds, report["expected_bounds_mm"], rtol=1e-6)
    assert mesh.volume == pytest.approx(report["expected_volume_mm3"], rel=1e-6)
    assert len(mesh.faces) == report["validation"]["triangle_count"]
    assert report["validation"]["passed"]
    assert json.loads((output / "report.json").read_text()) == report
    assert report["input"]["sha256"] == hashlib.sha256((FIXTURES / fixture).read_bytes()).hexdigest()


@pytest.mark.parametrize("value", [0, 128, 255])
@pytest.mark.parametrize("mode,invert", [("relief", False), ("relief", True), ("lithophane", False), ("lithophane", True)])
def test_constant_analytic_volume(tmp_path, value, mode, invert):
    source = tmp_path / "image.png"
    Image.new("L", (6, 3), value).save(source)
    params = Parameters(width=40, base=2, relief=5, resolution=10, mode=mode, invert=invert)
    report = convert(source, tmp_path / "out", params)
    intensity = value / 255
    if (mode == "lithophane") != invert:
        intensity = 1 - intensity
    thickness = 2 + 5 * intensity
    assert report["validation"]["volume_mm3"] == pytest.approx(40 * 20 * thickness, rel=1e-6)
    assert report["validation"]["dimensions_mm"] == pytest.approx([40, 20, thickness])


def test_orientation_and_preview(tmp_path):
    report = convert(FIXTURES / "orientation.png", tmp_path / "out", Parameters(resolution=20))
    mesh = independent_mesh(tmp_path / "out/model.stl")
    expected = [(0, 60, 0), (100, 60, 64), (0, 0, 192), (100, 0, 255)]
    for x, y, brightness in expected:
        heights = mesh.vertices[(mesh.vertices[:, 0] == x) & (mesh.vertices[:, 1] == y), 2]
        assert heights.max() == pytest.approx(1 + 3 * brightness / 255)
    preview = np.asarray(Image.open(tmp_path / "out/height.png"))
    assert preview[0, 0] == 0 and preview[0, -1] == 64
    assert preview[-1, 0] == 192 and preview[-1, -1] == 255
    assert report["sampling"]["grid_vertices_xy"] == [20, 12]


def test_transparency_on_white(tmp_path):
    convert(FIXTURES / "transparent.png", tmp_path / "out", Parameters(resolution=16))
    preview = np.asarray(Image.open(tmp_path / "out/height.png"))
    assert np.all(preview[:, :8] == 0)
    assert np.all(preview[:, 8:] == 255)


def test_exif_orientation(tmp_path):
    image = Image.new("RGB", (12, 8), "black")
    image.paste("white", (0, 0, 6, 8))
    exif = image.getexif()
    exif[274] = 6  # clockwise 90 degrees
    source = tmp_path / "oriented.jpg"
    image.save(source, exif=exif, quality=100, subsampling=0)
    report = convert(source, tmp_path / "out", Parameters(resolution=12))
    assert report["input"]["oriented_size_px"] == [8, 12]
    assert report["validation"]["dimensions_mm"][:2] == [100, 150]
    preview = np.asarray(Image.open(tmp_path / "out/height.png"))
    assert preview[0].min() > 250 and preview[-1].max() < 5


def test_deterministic_bundle(tmp_path):
    for directory in ("a", "b"):
        convert(FIXTURES / "gradient.png", tmp_path / directory, Parameters(resolution=20))
    for name in ("model.stl", "height.png", "report.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


@pytest.mark.parametrize("changes", [{"width": 0}, {"width": -1}, {"width": float("nan")},
    {"base": 0}, {"base": float("inf")}, {"relief": -1}, {"relief": 101},
    {"resolution": 1}, {"resolution": 257}, {"resolution": 3.5}, {"resolution": True},
    {"mode": "unknown"}, {"invert": 1}])
def test_invalid_parameters(tmp_path, changes):
    with pytest.raises(ValueError):
        convert(FIXTURES / "gray.png", tmp_path / "out", replace(Parameters(), **changes))
    assert not (tmp_path / "out").exists()


def test_bad_aspect_dimensions(tmp_path):
    with pytest.raises(ValueError, match="aspect-preserving"):
        convert(FIXTURES / "tall.png", tmp_path / "out")


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n"])
def test_corrupt_image(tmp_path, data):
    source = tmp_path / "bad.png"
    source.write_bytes(data)
    assert main(["convert", str(source), "--output", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()


def test_truncated_image(tmp_path):
    source = tmp_path / "bad.png"
    source.write_bytes((FIXTURES / "gradient.png").read_bytes()[:50])
    with pytest.raises(ValueError):
        convert(source, tmp_path / "out")


def test_png_crc_rejected(tmp_path):
    source = tmp_path / "bad-crc.png"
    data = bytearray((FIXTURES / "gradient.png").read_bytes())
    # Corrupt IDAT's checksum, leaving the compressed pixels decodable.
    position = data.index(b"IDAT")
    length = struct.unpack_from(">I", data, position - 4)[0]
    data[position + 4 + length] ^= 1
    source.write_bytes(data)
    with pytest.raises(ValueError, match="cannot decode"):
        convert(source, tmp_path / "out")


def test_palette_transparency_and_one_pixel(tmp_path):
    source = tmp_path / "palette.png"
    image = Image.new("P", (1, 1), 0)
    image.putpalette([0] * 768)
    image.save(source, transparency=0)
    report = convert(source, tmp_path / "out", Parameters(resolution=2))
    assert report["validation"]["dimensions_mm"] == [100, 100, 4]
    assert report["validation"]["triangle_count"] == 12


def test_entirely_inward_mesh_fails_positive_volume(tmp_path):
    convert(FIXTURES / "gray.png", tmp_path / "out", Parameters(resolution=2))
    path = tmp_path / "out/model.stl"
    data = bytearray(path.read_bytes())
    count = struct.unpack_from("<I", data, 80)[0]
    for i in range(count):
        start = 84 + i * 50
        data[start + 24:start + 36], data[start + 36:start + 48] = data[start + 36:start + 48], data[start + 24:start + 36]
        normal = struct.unpack_from("<fff", data, start)
        struct.pack_into("<fff", data, start, *(-value for value in normal))
    path.write_bytes(data)
    report = validate_stl(path)
    assert report["checks"]["watertight"] and report["checks"]["consistent_winding"]
    assert report["checks"]["stored_normals_match"]
    assert not report["checks"]["positive_volume"]
    assert not report["passed"]
    assert main(["validate", str(path)]) == 1


def test_validation_failure_leaves_no_bundle(tmp_path, monkeypatch):
    import image_relief_forge.conversion as module
    monkeypatch.setattr(module, "validate_stl", lambda *args, **kwargs: {"passed": False, "checks": {"test_failure": False}})
    with pytest.raises(ValueError, match="test_failure"):
        convert(FIXTURES / "gray.png", tmp_path / "out", Parameters(resolution=4))
    assert list(tmp_path.iterdir()) == []


def test_resource_limits(tmp_path, monkeypatch):
    import image_relief_forge.conversion as module
    monkeypatch.setattr(module, "MAX_INPUT_BYTES", 10)
    with pytest.raises(ValueError, match="byte limit"):
        convert(FIXTURES / "gray.png", tmp_path / "out")
    monkeypatch.setattr(module, "MAX_INPUT_BYTES", 32 * 1024 * 1024)
    monkeypatch.setattr(module, "MAX_INPUT_PIXELS", 10)
    with pytest.raises(ValueError, match="pixel limit"):
        convert(FIXTURES / "gray.png", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_formats_rejected(tmp_path):
    for name, image in [("unsupported.bmp", Image.new("RGB", (4, 4))),
                         ("sixteen.png", Image.new("I;16", (4, 4)))]:
        source = tmp_path / name
        image.save(source)
        with pytest.raises(ValueError):
            convert(source, tmp_path / "out")


def test_animated_png_rejected(tmp_path):
    source = tmp_path / "animated.png"
    Image.new("L", (4, 4), 0).save(source, save_all=True, append_images=[Image.new("L", (4, 4), 255)])
    with pytest.raises(ValueError, match="animated"):
        convert(source, tmp_path / "out")


def test_no_overwrite(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "precious.txt").write_text("keep me")
    assert main(["convert", str(FIXTURES / "gray.png"), "--output", str(output)]) == 2
    assert list(output.iterdir()) == [output / "precious.txt"]


def test_maximum_resolution_and_zero_relief(tmp_path):
    source = tmp_path / "square.png"
    Image.new("L", (2, 2), 255).save(source)
    report = convert(source, tmp_path / "out", Parameters(resolution=256, relief=0))
    assert report["validation"]["triangle_count"] == 262140
    assert report["validation"]["volume_mm3"] == pytest.approx(10000)
    assert independent_mesh(tmp_path / "out/model.stl").is_watertight


def test_validate_cli(tmp_path, capsys):
    convert(FIXTURES / "gray.png", tmp_path / "out", Parameters(resolution=4))
    assert main(["validate", str(tmp_path / "out/model.stl")]) == 0
    assert json.loads(capsys.readouterr().out)["passed"]


@pytest.mark.parametrize("damage", ["missing", "reversed", "degenerate", "nonfinite", "length", "normal", "bounds", "duplicate"])
def test_validation_detects_damage(tmp_path, damage):
    report = convert(FIXTURES / "gray.png", tmp_path / "out", Parameters(resolution=4))
    path = tmp_path / "out/model.stl"
    data = bytearray(path.read_bytes())
    count = struct.unpack_from("<I", data, 80)[0]
    if damage == "missing":
        data = data[:-50]
        struct.pack_into("<I", data, 80, count - 1)
    elif damage == "reversed":
        data[108:120], data[120:132] = data[120:132], data[108:120]
    elif damage == "degenerate":
        data[108:120] = data[96:108]
    elif damage == "nonfinite":
        struct.pack_into("<f", data, 96, float("nan"))
    elif damage == "length":
        data.append(0)
    elif damage == "normal":
        struct.pack_into("<fff", data, 84, 0, 0, 0)
    elif damage == "duplicate":
        data.extend(data[84:134])
        struct.pack_into("<I", data, 80, count + 1)
    elif damage == "bounds":
        for i in range(count):
            for j in range(3):
                offset = 84 + 50 * i + 12 + 12 * j
                struct.pack_into("<f", data, offset, struct.unpack_from("<f", data, offset)[0] + 1)
    path.write_bytes(data)
    if damage in ("nonfinite", "length"):
        with pytest.raises(ValueError):
            validate_stl(path)
    else:
        result = validate_stl(path, expected_bounds=report["expected_bounds_mm"])
        assert not result["passed"]
