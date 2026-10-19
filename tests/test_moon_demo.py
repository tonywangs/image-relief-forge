"""Guard the independent slicer evidence against misleading success reports."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location('demo_moon', Path(__file__).parents[1] / 'scripts/demo_moon.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def toolpaths():
    lines = ['G21 ; mm', 'G90 ; absolute XYZ', 'M83 ; relative E']
    for layer in range(1, 17):
        z = layer * .2
        lines += [f';Z:{z}', f'G1 X5 Y5 Z{z}', 'G1 X6 Y6 E1',
                  'G1 X68 Y5', 'G1 X69 Y6 E1']
    return '\n'.join(lines)


PLACEMENTS = [dict(id='left', lower_left_mm=[5, 5], footprint_mm=[60, 60]),
              dict(id='right', lower_left_mm=[68, 5], footprint_mm=[60, 60])]


def inspect(tmp_path, text):
    path = tmp_path/'test.gcode'
    path.write_text(text)
    return demo.inspect_gcode(path, PLACEMENTS, tmp_path/'preview.svg')


def test_accepts_two_tiles_and_16_layers(tmp_path):
    result = inspect(tmp_path, toolpaths())
    assert result['extrusion_segments_by_tile'] == {'left': 16, 'right': 16}
    assert result['layer_count'] == 16


@pytest.mark.parametrize('old,new', [
    ('G1 X69 Y6 E1', 'G1 X134 Y6 E1'),  # outside bed
    ('G1 X68 Y5', 'G1 X68 Y5 E1'),  # extrusion bridges the reserved gap
    ('G1 X69 Y6 E1', 'G1 X69 Y6'),  # second tile never prints
    ('M83 ; relative E', 'M82 ; absolute E'),
    (';Z:3.2', ';Z:3.4'),
])
def test_rejects_invalid_slicer_evidence(tmp_path, old, new):
    with pytest.raises(AssertionError):
        inspect(tmp_path, toolpaths().replace(old, new))


def test_canonical_does_not_hide_displacement():
    triangles = np.array([[[0., 0, 0], [1, 0, 0], [0, 1, 0]]])
    np.testing.assert_array_equal(demo.canonical(triangles), demo.canonical(triangles[:, ::-1]))
    assert not np.array_equal(demo.canonical(triangles), demo.canonical(triangles + .01))
