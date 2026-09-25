"""Bounded seeded round trips and adversarial failures for the 3MF profile."""
import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image
import pytest

from image_relief_forge.conversion import Parameters, convert
from image_relief_forge import three_mf as fmt
from image_relief_forge.validation_3mf import validate_3mf


@pytest.mark.parametrize("seed", range(200))
def test_seeded_roundtrip(tmp_path, seed):
    rng = np.random.default_rng(seed)
    # Odd dimensions, extreme aspect ratios, alpha, and asymmetric markers.
    sizes = [(17, 13), (3, 63), (63, 3), (11, 19), (21, 21)]
    w, h = sizes[seed % len(sizes)]
    pixels = rng.integers(0, 256, (h, w, 4), dtype=np.uint8)
    pixels[0, 0] = [0, 0, 0, 255]
    pixels[-1, -1] = [255, 255, 255, 255]
    pixels[0, -1] = [70, 70, 70, 255]
    image = tmp_path / 'input.png'
    Image.fromarray(pixels).save(image)
    width = float(rng.uniform(10, 80))
    resolution = int(rng.integers(9, 26))
    depth = width * h / w
    tiled = seed % 2 == 0
    params = Parameters(width=width, resolution=resolution, base=float(rng.uniform(.01, 3)),
                        relief=float(rng.uniform(0, 20)), mode='lithophane' if seed % 3 else 'relief',
                        invert=bool(seed % 4 == 0),
                        max_tile_width=width * .61 if tiled and w >= h else (width if tiled else None),
                        max_tile_height=depth * .61 if tiled and h >= w else (depth if tiled else None))
    hashes = []
    for repeat in range(2):
        output = tmp_path / str(repeat)
        report = convert(image, output, params, three_mf=True)
        check = report['artifacts']['3mf']['validation']
        assert check['passed'], check
        if tiled:
            assert check['assembly']['checks']['complete_coverage_without_overlap']
            assert check['assembly']['checks']['seam_profiles']
            assert check['assembly']['shared_vertex_observations'] > 0
        hashes.append({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()})
    assert hashes[0] == hashes[1]


@pytest.fixture
def bundle(tmp_path):
    out = tmp_path / 'bundle'
    convert('fixtures/orientation.png', out, Parameters(resolution=9), three_mf=True)
    return out


def rewrite(bundle, change):
    path = bundle / 'model.3mf'
    with zipfile.ZipFile(path) as z:
        parts = [z.read(name) for name in fmt.PARTS]
    changed = change(parts[2])
    assert changed != parts[2], "test mutation must alter the package"
    parts[2] = changed
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in zip(fmt.PARTS, parts):
            z.writestr(name, data)


@pytest.mark.parametrize('old,new', [
    (b'unit="millimeter"', b'unit="inch"'),
    (b'objectid="1"', b'objectid="999"'),
    (b'1 0 0 0 1 0 0 0 1 0 0 0', b'1 0 0 0 1 0 0 0 1 5 0 0'),
    (b'name="model"', b'name="wrong"'),
    (b'x="0"', b'x="nan"'),
    (b'v1="1"', b'v1="99999999"'),
])
def test_tampered_packages(bundle, old, new):
    rewrite(bundle, lambda xml: xml.replace(old, new, 1))
    try:
        result = validate_3mf(bundle)
    except ValueError:
        return
    assert not result['passed']


def test_geometry_tampering(bundle):
    rewrite(bundle, lambda xml: xml.replace(b'z="0"', b'z="0.001"', 1))
    assert not validate_3mf(bundle)['passed']


def test_collision_preserves_existing(bundle):
    before = (bundle / 'model.3mf').read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        fmt.export_3mf(bundle)
    assert (bundle / 'model.3mf').read_bytes() == before
    with pytest.raises(ValueError, match='already exists'):
        convert('fixtures/black.png', bundle, three_mf=True)
    assert (bundle / 'model.3mf').read_bytes() == before


@pytest.mark.parametrize('limit', ['MAX_BYTES', 'MAX_TRIANGLES', 'MAX_MESHES', 'MAX_VERTICES', 'MAX_MESH_TRIANGLES'])
def test_resource_failure_is_transactional(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(fmt, limit, 1)
    params = Parameters(resolution=9, max_tile_width=60, max_tile_height=60)
    out = tmp_path / 'failed'
    with pytest.raises(ValueError):
        convert('fixtures/orientation.png', out, params, three_mf=True)
    assert list(tmp_path.iterdir()) == []


def test_serialization_failure_is_transactional(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('simulated disk failure')
    monkeypatch.setattr(zipfile.ZipFile, 'writestr', fail)
    out = tmp_path / 'failed'
    with pytest.raises(OSError, match='simulated'):
        convert('fixtures/black.png', out, Parameters(resolution=4), three_mf=True)
    assert list(tmp_path.iterdir()) == []


def test_validation_failure_is_transactional(tmp_path, monkeypatch):
    from image_relief_forge import validation_3mf
    monkeypatch.setattr(validation_3mf, 'validate_3mf', lambda _: {'passed': False})
    with pytest.raises(ValueError, match='round-trip'):
        convert('fixtures/black.png', tmp_path / 'failed', Parameters(resolution=4), three_mf=True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('value', [1, 'yes', None])
def test_bad_setting(tmp_path, value):
    with pytest.raises(ValueError, match='boolean'):
        convert('fixtures/black.png', tmp_path / 'bad', three_mf=value)


def test_profile_zip_metadata(bundle):
    with zipfile.ZipFile(bundle / 'model.3mf') as z:
        assert z.namelist() == list(fmt.PARTS)
        for info in z.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.external_attr == 0o100644 << 16
            assert not info.extra and not info.comment


def test_no_stl_semantic_changes(tmp_path):
    for tiled in (False, True):
        p = Parameters(resolution=9, max_tile_width=60 if tiled else None, max_tile_height=60 if tiled else None)
        plain, extra = tmp_path / f'plain{tiled}', tmp_path / f'extra{tiled}'
        before = convert('fixtures/transparent.png', plain, p)
        after = convert('fixtures/transparent.png', extra, p, three_mf=True)
        del after['artifacts']['3mf']
        assert before == after
        for path in plain.iterdir():
            if path.name != 'report.json':
                assert path.read_bytes() == (extra / path.name).read_bytes()


@pytest.mark.parametrize('kind', ['duplicate', 'compressed', 'truncated', 'doctype', 'extra-part'])
def test_malformed_zip(bundle, kind):
    path = bundle / 'model.3mf'
    if kind == 'truncated':
        path.write_bytes(path.read_bytes()[:100])
    elif kind == 'doctype':
        rewrite(bundle, lambda xml: xml.replace(b'<model ', b'<!DOCTYPE model><model ', 1))
    else:
        with zipfile.ZipFile(path) as z:
            data = [(i.filename, z.read(i.filename)) for i in z.infolist()]
        if kind == 'extra-part':
            data.append(('other.xml', b'<a/>'))
        if kind == 'duplicate':
            data.append(data[-1])
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED if kind == 'compressed' else zipfile.ZIP_STORED) as z:
            for name, contents in data:
                if kind == 'duplicate' and name == data[-1][0] and name in z.namelist():
                    with pytest.warns(UserWarning, match='Duplicate'):
                        z.writestr(name, contents)
                else:
                    z.writestr(name, contents)
    with pytest.raises(ValueError):
        validate_3mf(bundle)


def test_256_tiles_and_flat_surface(tmp_path):
    source = tmp_path / 'square.png'
    Image.new('L', (17, 17), 127).save(source)
    report = convert(source, tmp_path / 'out', Parameters(width=16, resolution=17, relief=0,
                     max_tile_width=1, max_tile_height=1), three_mf=True)
    assert report['artifacts']['3mf']['mesh_count'] == 256
    assert report['artifacts']['3mf']['validation']['passed']


@pytest.mark.parametrize('size,width', [((1, 19999), .1), ((19999, 1), 2000)])
def test_extreme_supported_aspects(tmp_path, size, width):
    source = tmp_path / 'extreme.png'
    Image.new('RGBA', size, (15, 31, 127, 103)).save(source)
    report = convert(source, tmp_path / 'out', Parameters(width=width, resolution=33), three_mf=True)
    assert report['artifacts']['3mf']['validation']['passed']


def test_oversized_reference_rejected_before_copy(tmp_path):
    source = tmp_path / 'square.png'
    Image.new('L', (5, 5), 127).save(source)
    out = tmp_path / 'out'
    convert(source, out, Parameters(resolution=5, max_tile_width=100, max_tile_height=100), three_mf=True)
    with (out / 'surface.npy').open('wb') as stream:
        stream.truncate(256 * 256 * 8 + 1025)
    with pytest.raises(ValueError, match='oversized'):
        validate_3mf(out)


def test_missing_optional_reader_is_transactional(tmp_path, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'lib3mf', None)
    with pytest.raises(ValueError, match='three-mf extra'):
        convert('fixtures/black.png', tmp_path / 'failed', Parameters(resolution=4), three_mf=True)
    assert list(tmp_path.iterdir()) == []
