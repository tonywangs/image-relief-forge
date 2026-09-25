"""Seeded end-to-end layouts and adversarial geometry/transaction checks."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from PIL import Image
import pytest

from image_relief_forge.conversion import Parameters, convert
from image_relief_forge.layout import LayoutParameters, create_layout, pack
from image_relief_forge.validation_layout import validate_layout
from image_relief_forge import layout, three_mf


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob('*') if p.is_file()}


@pytest.fixture
def source(tmp_path):
    out = tmp_path / 'source'
    convert(Path(__file__).resolve().parents[1] / 'fixtures/orientation.png', out,
            Parameters(width=60, resolution=9, max_tile_width=25, max_tile_height=25))
    return out


@pytest.mark.parametrize('seed', range(200))
def test_seeded_roundtrip(seed, tmp_path):
    rng = np.random.default_rng(seed)
    # Every image has an asymmetric marker plus a seeded uneven brightness field.
    pixels = rng.integers(0, 256, (7, 11), dtype=np.uint8)
    pixels[0, :3] = [0, 127, 255]
    pixels[-1, -2:] = [63, 191]
    image = tmp_path / 'input.png'
    Image.fromarray(pixels).save(image)
    width = float(rng.integers(20, 150))
    source = tmp_path / 'source'
    convert(image, source, Parameters(width=width, resolution=11,
            max_tile_width=width * float(rng.choice([.21, .31, .41, .61])),
            max_tile_height=width * float(rng.choice([.21, .31, .41]))))
    original = hashes(source)
    tiles = json.loads((source / 'manifest.json').read_text())['tiles']
    sizes = np.array([three_mf.read_stl(source / t['file']).max(axis=(0, 1))[:2] for t in tiles], dtype=float)
    margin, clearance = float(rng.choice([0, .5, 2])), float(rng.choice([0, .3, 1.5]))
    # Integer multipliers include exactly fitted boundaries and multiple beds.
    p = LayoutParameters(float(sizes[:, 0].max()) * int(rng.integers(1, 4)) + 2 * margin,
                         float(sizes[:, 1].max()) * int(rng.integers(1, 4)) + 2 * margin,
                         margin, clearance, bool(seed % 2))
    a, b = tmp_path / 'a', tmp_path / 'b'
    result = create_layout(source, a, p)
    create_layout(source, b, p)
    assert hashes(a) == hashes(b)
    assert hashes(source) == original
    check = validate_layout(a)
    assert check['passed'], check
    assert result['tile_count'] == len(tiles)
    assert all(0 < bed['utilization'] <= 1 + 1e-12 for bed in result['beds'])
    # The validator is deliberately independent of the packing heuristic.
    assert {t['id'] for t in result['placements']} == {t['id'] for t in tiles}


def test_rotation_only_and_exact_fit(tmp_path):
    image = tmp_path / 'input.png'
    Image.fromarray(np.array([[0, 40, 255], [150, 75, 200]], dtype=np.uint8)).save(image)
    source = tmp_path / 'source'
    convert(image, source, Parameters(width=30, resolution=3, max_tile_width=30, max_tile_height=20))
    with pytest.raises(ValueError, match='cannot fit individually'):
        create_layout(source, tmp_path / 'no', LayoutParameters(22, 32, margin=1))
    out = tmp_path / 'yes'
    m = create_layout(source, out, LayoutParameters(22, 32, margin=1, rotate=True))
    assert m['placements'][0]['rotation_degrees'] == 90
    assert m['placements'][0]['lower_left_mm'] == [1, 1]
    assert m['beds'][0]['usable_utilization'] == 1
    assert validate_layout(out)['passed']


def test_stable_order_and_clearance():
    rectangles = [('b', 10., 5.), ('a', 10., 5.), ('c', 10., 5.)]
    p = LayoutParameters(23, 12, 1, 1)
    result = pack(rectangles, p)
    assert result == pack(rectangles[::-1], p)
    assert [t['id'] for t in result] == ['a', 'b', 'c']
    assert [t['bed'] for t in result] == [1, 1, 2]
    assert result[1]['lower_left_mm'] == [12, 1]
    assert pack([('a', 10, 10), ('b', 10, 10)], LayoutParameters(20, 10))[1]['bed'] == 1
    assert pack([('a', 10, 10), ('b', 10, 10)], LayoutParameters(20, 10, clearance=.00001))[1]['bed'] == 2


@pytest.mark.parametrize('field,value', [
    ('bed_width', 0), ('bed_height', -1), ('bed_width', 2001), ('bed_width', float('nan')),
    ('bed_height', float('inf')), ('margin', -1), ('margin', 10), ('clearance', -1),
    ('clearance', float('nan')), ('rotate', 1), ('max_tiles', 0), ('max_tiles', 257),
    ('max_beds', 0), ('max_beds', 257), ('max_beds', 1.5), ('max_tiles', True), ('margin', True)])
def test_invalid_parameters(field, value):
    with pytest.raises(ValueError):
        replace(LayoutParameters(20, 20), **{field: value}).validate()


def test_limits(source, tmp_path):
    with pytest.raises(ValueError, match='tile count'):
        create_layout(source, tmp_path / 'tiles', LayoutParameters(100, 100, max_tiles=1))
    with pytest.raises(ValueError, match='not proof of infeasibility'):
        create_layout(source, tmp_path / 'beds', LayoutParameters(25, 25, max_beds=1))
    assert not (tmp_path / 'tiles').exists() and not (tmp_path / 'beds').exists()
    assert not list(tmp_path.glob('.relief-layout-*'))
    assert len(pack([(str(i), 1, 1) for i in range(256)], LayoutParameters(1, 1))) == 256
    with pytest.raises(ValueError, match='tile count'):
        pack([(str(i), 1, 1) for i in range(257)], LayoutParameters(1, 1))


@pytest.mark.parametrize('kind', ['file', 'directory', 'symlink'])
def test_collision(source, tmp_path, kind):
    out = tmp_path / 'out'
    if kind == 'file':
        out.write_text('preserve')
    elif kind == 'directory':
        out.mkdir()
        (out / 'sentinel').write_text('preserve')
    else:
        out.symlink_to(tmp_path / 'missing')
    with pytest.raises(ValueError, match='already exists'):
        create_layout(source, out, LayoutParameters(100, 100))
    if kind == 'file':
        assert out.read_text() == 'preserve'
    elif kind == 'directory':
        assert (out / 'sentinel').read_text() == 'preserve'
    else:
        assert out.is_symlink()


@pytest.mark.parametrize('failure', ['json', 'xml', 'preview', 'move', 'copy', 'validate'])
def test_transaction_cleanup(source, tmp_path, monkeypatch, failure):
    def fail(*a, **kw):
        raise OSError('injected failure')
    if failure == 'json':
        monkeypatch.setattr(layout, 'write_json', fail)
    elif failure == 'xml':
        monkeypatch.setattr(three_mf, 'number', fail)
    elif failure == 'preview':
        monkeypatch.setattr(layout, 'preview', fail)
    elif failure == 'copy':
        monkeypatch.setattr(shutil, 'copyfile', fail)
    elif failure == 'validate':
        import image_relief_forge.validation_layout as validator
        monkeypatch.setattr(validator, 'validate_layout', fail)
    else:
        original = shutil.move
        calls = []
        def move(*a, **kw):
            calls.append(1)
            if len(calls) == 2:
                fail()
            return original(*a, **kw)
        monkeypatch.setattr(shutil, 'move', move)
    before = hashes(source)
    out = tmp_path / 'out'
    with pytest.raises(OSError, match='injected'):
        create_layout(source, out, LayoutParameters(100, 100))
    assert not out.exists() and not list(tmp_path.glob('.relief-layout-*'))
    assert hashes(source) == before


def test_racing_output_creation(source, tmp_path, monkeypatch):
    import image_relief_forge.validation_layout as validator
    original = validator.validate_layout
    out = tmp_path / 'out'
    def racing(*args):
        report = original(*args)
        out.mkdir()
        (out / 'sentinel').write_text('preserve')
        return report
    monkeypatch.setattr(validator, 'validate_layout', racing)
    with pytest.raises(FileExistsError):
        create_layout(source, out, LayoutParameters(100, 100))
    assert (out / 'sentinel').read_text() == 'preserve'
    assert list(out.iterdir()) == [out / 'sentinel']


def mutate_package(path, mutation):
    with zipfile.ZipFile(path) as z:
        parts = [(i, z.read(i.filename)) for i in z.infolist()]
    root = ET.fromstring(parts[-1][1])
    mutation(root)
    with zipfile.ZipFile(path, 'w') as z:
        for info, data in parts[:-1]:
            z.writestr(info, data)
        z.writestr(parts[-1][0], ET.tostring(root))


@pytest.mark.parametrize('mutation,check', [
    ('overlap', 'pairwise_clearance'), ('outside', 'bed_containment'), ('lift', 'bed_contact'),
    ('vertex', 'source_triangles'), ('inverse', 'assembly_reconstruction'),
    ('size', 'footprint_metadata'), ('utilization', 'utilization'), ('hash', 'artifact_hashes')])
def test_detects_tampering(source, tmp_path, mutation, check):
    out = tmp_path / 'out'
    m = create_layout(source, out, LayoutParameters(100, 100, clearance=2, margin=1))
    if mutation in ('overlap', 'outside', 'lift', 'vertex'):
        def mutate(root):
            if mutation == 'vertex':
                vertex = root[0][0][0][0][0]
                vertex.set('z', str(float(vertex.get('z')) + .1))
            else:
                items = root[1]
                item = items[1] if mutation == 'overlap' else items[0]
                transform = item.get('transform').split()
                if mutation == 'overlap':
                    transform = items[0].get('transform').split()
                elif mutation == 'outside':
                    transform[9] = '150'
                else:
                    transform[11] = '1'
                item.set('transform', ' '.join(transform))
        path = out / m['beds'][0]['file']
        mutate_package(path, mutate)
        m['beds'][0]['sha256'] = layout.digest(path)  # prove geometry, not just hash checks
    elif mutation == 'inverse':
        m['placements'][0]['bed_to_assembly'][3][0] += 1
    elif mutation == 'size':
        m['placements'][0]['footprint_mm'][0] += 1
    elif mutation == 'utilization':
        m['beds'][0]['utilization'] += .1
    else:
        m['beds'][0]['sha256'] = '0' * 64
    layout.write_json(out / 'layout.json', m)
    report = validate_layout(out)
    assert not report['passed'] and not report['checks'][check]


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'wrong-unit', 'scale', 'unallowed-rotation', 'missing-bed', 'source-symlink'])
def test_rejects_invalid_bundle(source, tmp_path, mutation):
    out = tmp_path / 'out'
    m = create_layout(source, out, LayoutParameters(100, 100))
    if mutation == 'duplicate':
        m['placements'][1]['id'] = m['placements'][0]['id']
    elif mutation == 'missing':
        m['placements'].pop()
        m['tile_count'] -= 1
    elif mutation == 'unallowed-rotation':
        m['placements'][0]['rotation_degrees'] = 90
    elif mutation == 'missing-bed':
        (out / m['beds'][0]['file']).unlink()
    elif mutation == 'source-symlink':
        shutil.rmtree(out / 'source')
        (out / 'source').symlink_to(source)
    else:
        def mutate(root):
            if mutation == 'wrong-unit':
                root.set('unit', 'inch')
            else:
                values = root[1][0].get('transform').split()
                values[0] = '2'
                root[1][0].set('transform', ' '.join(values))
        mutate_package(out / m['beds'][0]['file'], mutate)
    layout.write_json(out / 'layout.json', m)
    with pytest.raises(ValueError):
        validate_layout(out)


def test_maximum_bed_count(tmp_path):
    pixels = np.arange(17*17, dtype=np.uint16).reshape(17, 17).astype(np.uint8)
    image = tmp_path / 'input.png'
    Image.fromarray(pixels).save(image)
    source = tmp_path / 'source'
    convert(image, source, Parameters(width=16, resolution=17, max_tile_width=1, max_tile_height=1))
    out = tmp_path / 'out'
    result = create_layout(source, out, LayoutParameters(1, 1))
    assert result['bed_count'] == result['tile_count'] == 256
    assert len(list(out.glob('bed-*.3mf'))) == 256
    assert validate_layout(out)['passed']


def test_svg_coordinates_and_labels(source, tmp_path):
    out = tmp_path / 'out'
    p = LayoutParameters(60, 80, margin=2, clearance=1, rotate=True)
    result = create_layout(source, out, p)
    ns = {'s': 'http://www.w3.org/2000/svg'}
    scale = 600 / max(p.bed_width, p.bed_height)
    for bed in result['beds']:
        svg = ET.parse(out / bed['preview']).getroot()
        text = ' '.join(svg.itertext())
        assert f'Bed {bed["id"]}' in text
        rectangles = svg.findall('.//s:rect', ns)[3:]
        placements = [t for t in result['placements'] if t['bed'] == bed['id']]
        assert len(rectangles) == len(placements)
        for rect, placement in zip(rectangles, placements):
            assert placement['id'] in text
            x, y = placement['lower_left_mm']
            w, h = placement['footprint_mm']
            assert float(rect.get('x')) == pytest.approx(25 + x*scale)
            assert float(rect.get('y')) == pytest.approx(70 + (p.bed_height-y-h)*scale)
            assert float(rect.get('width')) == pytest.approx(w*scale)
            assert float(rect.get('height')) == pytest.approx(h*scale)


def test_decimal_exact_fit():
    # Inset-local packing must not lose an exact fit to subtract/add cancellation.
    for margin in [0.1, 0.3, 1.1, 2.7]:
        p = LayoutParameters(20.2, 30.7, margin=margin, max_beds=1)
        result = pack([('tile', p.bed_width-2*margin, p.bed_height-2*margin)], p)
        assert result[0]['bed'] == 1
