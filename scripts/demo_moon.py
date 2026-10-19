"""Reproduce the real-photo example and inspect it with independent PrusaSlicer.

Default: use a temporary directory; --output retains a new bundle for viewing.
Requires the test extra (Trimesh) and PrusaSlicer on PATH or --slicer.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA256 = 'b74f54a459dac07197be97edd8b380c6db26d5b96b1f8a647bcdef84516f175c'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    result = subprocess.run([str(x) for x in args], cwd=ROOT, capture_output=True,
                            text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(f'{args}: exit {result.returncode}\n{result.stdout}\n{result.stderr}')
    return result.stdout


def cli(*args):
    return run([sys.executable, '-m', 'image_relief_forge', *args])


def canonical(triangles):
    # Ignore face order and cyclic vertex order, but retain every coordinate/face.
    triangles = np.round(triangles, 5)
    triangles = np.array([t[np.lexsort(t.T[::-1])] for t in triangles]).reshape(-1, 9)
    return triangles[np.lexsort(triangles.T[::-1])]


def inspect_gcode(path, placements, preview):
    """Inspect this profile's absolute-mm XYZ, relative-E linear toolpaths.

    Not a general G-code safety validator. Reject unsupported motion modes.
    Extruding segments must stay within one assigned footprint; travel is omitted.
    """
    text = path.read_text()
    assert 'G21 ' in text and 'G90 ' in text and 'M83 ' in text
    position = np.zeros(3)
    layers = []
    counts = {p['id']: 0 for p in placements}
    points = []
    paths = []
    for line in text.splitlines():
        if line.startswith(';Z:'):
            layers.append(float(line[3:]))
        code = line.split(';')[0].strip().split()
        if not code:
            continue
        assert code[0] not in {'G20', 'G91', 'M82', 'G2', 'G3', 'G92'}, line
        if code[0] not in {'G0', 'G1'}:
            continue
        values = {s[0]: float(s[1:]) for s in code[1:] if s[0] in 'XYZE'}
        end = np.array([values.get(a, position[i]) for i, a in enumerate('XYZ')])
        if values.get('E', 0) > 0 and np.linalg.norm(end[:2] - position[:2]) > 0:
            owners = []
            for p in placements:
                lo = np.array(p['lower_left_mm'])
                hi = lo + p['footprint_mm']
                if np.all(np.array([position[:2], end[:2]]) >= lo - .02) and np.all(np.array([position[:2], end[:2]]) <= hi + .02):
                    owners.append(p['id'])
            assert len(owners) == 1, f'extrusion outside a unique footprint: {line}'
            counts[owners[0]] += 1
            points.extend([position.copy(), end.copy()])
            if abs(end[2] - 1.6) < 1e-6:
                paths.append(f'M{position[0]:.3f},{75-position[1]:.3f}L{end[0]:.3f},{75-end[1]:.3f}')
        position = end
    assert all(n > 0 for n in counts.values())
    np.testing.assert_allclose(layers, np.arange(1, 17) * .2, atol=1e-6)
    points = np.array(points)
    assert points[:, 2].min() >= .199 and points[:, 2].max() <= 3.201
    assert paths
    preview.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 133 85">'
        '<rect width="133" height="85" fill="white"/>'
        '<rect width="133" height="75" fill="#f3f6f7" stroke="#687580" stroke-width=".2"/>'
        '<path d="' + ''.join(paths) + '" fill="none" stroke="#126879" stroke-width=".16"/>'
        '<text x="3" y="81" font-size="3">PrusaSlicer extrusion paths at Z=1.6 mm (not a print)</text></svg>\n')
    return dict(layer_count=len(layers), layer_z_mm=layers, extrusion_segments_by_tile=counts,
                extrusion_bounds_mm=[points.min(axis=0).tolist(), points.max(axis=0).tolist()],
                gcode_sha256=sha(path), preview_sha256=sha(preview))


def build(output, slicer):
    source = ROOT / 'examples/moon/source.png'
    assert sha(source) == SOURCE_SHA256, 'source image checksum mismatch'
    output.mkdir(parents=True, exist_ok=False)
    conversion = ['--width', '120', '--base', '.8', '--relief', '2.4', '--resolution', '129',
                  '--mode', 'lithophane', '--max-tile-width', '60', '--max-tile-height', '60', '--3mf']
    layout_args = ['--bed-width', '133', '--bed-height', '75', '--margin', '5', '--clearance', '3', '--rotate']
    cli('convert', source, '--output', output/'tiles', *conversion)
    cli('layout', output/'tiles', '--output', output/'beds', *layout_args)
    cli('validate-assembly', output/'tiles')
    cli('validate-layout', output/'beds')
    # Repeat in isolation and compare every exported byte, not just manifest hashes.
    with tempfile.TemporaryDirectory(prefix='relief-moon-repeat-') as repeat_dir:
        repeat = Path(repeat_dir)
        cli('convert', source, '--output', repeat/'tiles', *conversion)
        cli('layout', repeat/'tiles', '--output', repeat/'beds', *layout_args)
        for folder in ('tiles', 'beds'):
            first = {str(p.relative_to(output/folder)): sha(p) for p in (output/folder).rglob('*') if p.is_file()}
            second = {str(p.relative_to(repeat/folder)): sha(p) for p in (repeat/folder).rglob('*') if p.is_file()}
            assert first == second, f'{folder} export is not deterministic'
    manifest = json.loads((output/'beds/layout.json').read_text())
    assert manifest['tile_count'] == 4 and manifest['bed_count'] == 2
    assert len({p['id'] for p in manifest['placements']}) == 4
    records = []
    inspection = output/'slicer'
    inspection.mkdir()
    version = run([slicer, '--help']).splitlines()[0]
    for bed in manifest['beds']:
        name = Path(bed['file']).stem
        package = output/'beds'/bed['file']
        placements = [p for p in manifest['placements'] if p['bed'] == bed['id']]
        info = run([slicer, '--info', package])
        assert info.count('manifold = yes') == 2
        sizes = [float(x) for x in re.findall(r'size_[xy] = ([\d.]+)', info)]
        np.testing.assert_allclose(sizes, [60]*4)
        (inspection/f'{name}-info.txt').write_text(info)
        roundtrip = inspection/f'{name}.stl'
        run([slicer, '--dont-arrange', '--export-stl', '--output', roundtrip, package])
        actual = trimesh.load(roundtrip, process=False)
        expected = []
        volumes = []
        for p in placements:
            mesh = trimesh.load(output/'tiles'/p['source_file'], process=False)
            # Exact vertex indexing only: no cleanup, rounding weld or repairs.
            vertices, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
            indexed = trimesh.Trimesh(vertices=vertices, faces=inverse[mesh.faces], process=False)
            assert indexed.is_watertight and indexed.is_winding_consistent and indexed.volume > 0
            np.testing.assert_allclose(indexed.extents, [60, 60, 3.2], atol=1e-6)
            transform = np.array(p['local_to_bed'])
            expected.append(mesh.triangles @ transform[:3] + transform[3])
            volumes.append(float(indexed.volume))
        np.testing.assert_allclose(canonical(actual.triangles), canonical(np.concatenate(expected)), atol=1e-5, rtol=0)
        np.testing.assert_allclose(actual.bounds, [[5, 5, 0], [128, 65, 3.2]], atol=1e-6)
        gcode = inspection/f'{name}.gcode'
        stdout = run([slicer, '--dont-arrange', '--load', ROOT/'examples/moon/inspection.ini',
                      '--threads', '2', '--export-gcode', '--output', gcode, package])
        (inspection/f'{name}-slice.txt').write_text(stdout)
        record = inspect_gcode(gcode, placements, inspection/f'{name}-layer.svg')
        record.update(bed=bed['id'], package_sha256=sha(package),
                      slicer_roundtrip_bounds_mm=actual.bounds.tolist(),
                      slicer_roundtrip_triangles=len(actual.faces),
                      roundtrip_triangles_match=True, source_volumes_mm3=volumes)
        records.append(record)
    report = dict(source_sha256=SOURCE_SHA256, slicer=version, dimensions_mm=[120, 120, 3.2],
                  tiles=4, beds=2, margin_mm=5, clearance_mm=3, repeat_bundles_identical=True,
                  inspection_profile_sha256=sha(ROOT/'examples/moon/inspection.ini'), inspections=records,
                  limitations=['Headless PrusaSlicer CLI inspection; no GUI inspection or physical printing.',
                               'Generic inspection profile; G-code is not machine-qualified.',
                               'G-code bytes include slicer timestamps and are not deterministic.'])
    (output/'inspection.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='retain files in a NEW directory')
    parser.add_argument('--slicer', default='prusa-slicer')
    args = parser.parse_args()
    slicer = shutil.which(args.slicer)
    if slicer is None:
        parser.error('PrusaSlicer is required: install it or pass --slicer /path/to/prusa-slicer')
    if args.output:
        result = build(args.output.resolve(), slicer)
    else:
        with tempfile.TemporaryDirectory(prefix='relief-moon-') as temp:
            result = build(Path(temp)/'demo', slicer)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
