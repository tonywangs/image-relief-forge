"""Deterministic first-fit shelves for rectangular, already sampled relief tiles."""

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np

from .assembly import validate_assembly
from .three_mf import read_stl, source_entries

MAX_TILES = 256
MAX_BEDS = 256


@dataclass(frozen=True)
class LayoutParameters:
    bed_width: float
    bed_height: float
    margin: float = 0
    clearance: float = 0
    rotate: bool = False
    max_tiles: int = MAX_TILES
    max_beds: int = MAX_BEDS

    def validate(self):
        for name in ('bed_width', 'bed_height', 'margin', 'clearance'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not np.isfinite(value):
                raise ValueError(f'{name} must be finite')
            if not 0 <= value <= 2000:
                raise ValueError(f'{name} must be between 0 and 2000 mm')
        if min(self.bed_width, self.bed_height) < .1 or 2 * self.margin >= min(self.bed_width, self.bed_height):
            raise ValueError('bed must have positive usable dimensions (bed sides at least 0.1 mm)')
        if not isinstance(self.rotate, bool):
            raise ValueError('rotate must be boolean')
        for name, limit in (('max_tiles', MAX_TILES), ('max_beds', MAX_BEDS)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError(f'{name} must be an integer in 1..{limit}')


def pack(rectangles, parameters):
    """Input: (id, width, height). Output: placement dictionaries in packing order.

    No geometric tolerance is used to make a rectangle fit. Footprints are the
    float32 STL bounds, promoted to float64 before placement arithmetic.
    """
    p = parameters
    p.validate()
    if not 1 <= len(rectangles) <= p.max_tiles:
        raise ValueError('tile count exceeds configured limit or is empty')
    seen = set()
    for name, w, h in rectangles:
        if not isinstance(name, str) or name in seen or not np.isfinite([w, h]).all() or min(w, h) <= 0:
            raise ValueError('invalid rectangle or duplicate identity')
        seen.add(name)
    usable_w, usable_h = p.bed_width - 2 * p.margin, p.bed_height - 2 * p.margin
    beds, placements = [], []
    ordered = sorted(rectangles, key=lambda t: (-max(t[1:]), -t[1] * t[2], t[0]))
    for name, w, h in ordered:
        orientations = [(w, h, 0)] + ([(h, w, 90)] if p.rotate else [])
        orientations = sorted(orientations, key=lambda o: (o[1], o[0], o[2]))
        orientations = [o for o in orientations if o[0] <= usable_w and o[1] <= usable_h]
        if not orientations:
            raise ValueError(f'tile {name} cannot fit individually on the usable bed')
        selected = None
        for bed_index in range(len(beds) + 1):
            if bed_index == len(beds):
                if len(beds) >= p.max_beds:
                    raise ValueError('heuristic exhausted max_beds; this is not proof of infeasibility')
                beds.append([])
            shelves = beds[bed_index]
            for shelf in shelves:
                for tw, th, angle in orientations:
                    if shelf['x'] + tw <= usable_w and th <= shelf['height']:
                        selected = (shelf, tw, th, angle)
                        break
                if selected:
                    break
            if selected is None:
                y = shelves[-1]['y'] + shelves[-1]['height'] + p.clearance if shelves else 0.0
                for tw, th, angle in orientations:
                    if y + th <= usable_h:
                        shelf = dict(x=0.0, y=y, height=th)
                        shelves.append(shelf)
                        selected = (shelf, tw, th, angle)
                        break
            if selected:
                shelf, tw, th, angle = selected
                placements.append(dict(id=name, bed=bed_index + 1, rotation_degrees=angle,
                                       lower_left_mm=[shelf['x'] + p.margin, shelf['y'] + p.margin], footprint_mm=[tw, th]))
                shelf['x'] += tw + p.clearance
                break
    return placements


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')


def preview(path, bed, placements, p):
    # Fixed-size drawing plus a full text key also labels very thin rectangles.
    scale = 600 / max(p.bed_width, p.bed_height)
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" width="920" '
             f'height="{720 + 22 * len(placements)}" viewBox="0 0 920 {720 + 22 * len(placements)}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#182b3a">',
             f'<text x="25" y="25">Bed {bed} · {p.bed_width:g} × {p.bed_height:g} mm · view from +Z</text>',
             '<text x="25" y="48">+X right, +Y up. Arrows show original tile +X direction.</text>',
             f'<rect x="25" y="70" width="{p.bed_width * scale}" height="{p.bed_height * scale}" fill="#eee" stroke="black"/>',
             f'<rect x="{25 + p.margin * scale}" y="{70 + p.margin * scale}" width="{(p.bed_width - 2*p.margin)*scale}" height="{(p.bed_height - 2*p.margin)*scale}" fill="none" stroke="#888" stroke-dasharray="4 4"/>']
    for index, tile in enumerate(placements, 1):
        x, y = tile['lower_left_mm']
        w, h = tile['footprint_mm']
        sx, sy = 25 + x * scale, 70 + (p.bed_height - y - h) * scale
        lines.append(f'<rect x="{sx}" y="{sy}" width="{w*scale}" height="{h*scale}" fill="#b9dbeb" stroke="#265a75"/>')
        if min(w, h) * scale >= 20:
            arrow = '↑' if tile['rotation_degrees'] else '→'
            lines.append(f'<text x="{sx + 3}" y="{sy + 16}">{index}{arrow}</text>')
        lines.append(f'<text x="25" y="{700 + index*22}">{index}: {tile["id"]} · {tile["rotation_degrees"]}° CCW · ({x:.9g}, {y:.9g}) · {w:.9g} × {h:.9g} mm</text>')
    lines.append('</g></svg>\n')
    path.write_text('\n'.join(lines), encoding='utf-8')


def create_layout(source, output, parameters):
    """Create an independently verifiable bundle; never overwrite output."""
    from .three_mf import export_3mf
    from .validation_layout import validate_layout
    p = parameters
    p.validate()
    source, output = Path(source), Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('output directory already exists; choose a new directory')
    entries = source_entries(source)
    if not (source / 'manifest.json').is_file():
        raise ValueError('layout requires a tiled conversion bundle')
    if len(entries) > p.max_tiles:
        raise ValueError('tile count exceeds configured limit')
    if not validate_assembly(source)['passed']:
        raise ValueError('source assembly failed validation')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.relief-layout-', dir=output.parent) as temporary:
        stage = Path(temporary)
        saved = stage / 'source'
        saved.mkdir()
        for filename in ['manifest.json', 'surface.npy'] + [e[1] for e in entries]:
            shutil.copyfile(source / filename, saved / filename)
        # Validate the snapshot too, in case the source changed during copying.
        if not validate_assembly(saved)['passed']:
            raise ValueError('source snapshot failed validation')
        rectangles = []
        for name, filename, _ in entries:
            xyz = read_stl(saved / filename).astype(np.float64).reshape(-1, 3)
            if not np.array_equal(xyz.min(axis=0), [0, 0, 0]):
                raise ValueError('layout tiles must have local lower bounds at zero')
            w, h, _ = xyz.max(axis=0)
            rectangles.append((name, float(w), float(h)))
        placements = pack(rectangles, p)
        lookup = {name: (filename, origin) for name, filename, origin in entries}
        for tile in placements:
            name = tile['id']
            filename, origin = lookup[name]
            x, y = tile['lower_left_mm']
            rotation = np.eye(3) if tile['rotation_degrees'] == 0 else np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]])
            translation = np.array([x + (tile['footprint_mm'][0] if tile['rotation_degrees'] else 0), y, 0])
            tile['local_to_bed'] = np.vstack([rotation, translation]).tolist()
            tile['bed_to_assembly'] = np.vstack([rotation.T, np.asarray(origin) - translation @ rotation.T]).tolist()
            tile['source_file'] = filename
            tile['source_sha256'] = digest(saved / filename)
        beds = []
        for index in range(1, max(t['bed'] for t in placements) + 1):
            selected = [t for t in placements if t['bed'] == index]
            filename = f'bed-{index:03d}.3mf'
            export_3mf(saved, entries=[(t['id'], t['source_file'], [0, 0, 0]) for t in selected],
                        transforms=[t['local_to_bed'] for t in selected], destination=stage / filename)
            svg = f'bed-{index:03d}.svg'
            preview(stage / svg, index, selected, p)
            area = sum(t['footprint_mm'][0] * t['footprint_mm'][1] for t in selected)
            beds.append(dict(id=index, file=filename, sha256=digest(stage / filename), preview=svg,
                             preview_sha256=digest(stage / svg), tile_count=len(selected),
                             utilization=area / (p.bed_width * p.bed_height),
                             usable_utilization=area / ((p.bed_width-2*p.margin)*(p.bed_height-2*p.margin))))
        manifest = dict(schema_version=1, units='mm', algorithm='first-fit-fixed-shelves-v1',
                        parameters=asdict(p), bed_count=len(beds), tile_count=len(placements),
                        transform_convention='row vector: point @ first_three_rows + fourth_row; mm',
                        source_manifest_sha256=digest(saved / 'manifest.json'), beds=beds, placements=placements)
        write_json(stage / 'layout.json', manifest)
        report = validate_layout(stage)
        if not report['passed']:
            raise ValueError(f'layout validation failed: {report["checks"]}')
        write_json(stage / 'validation.json', report)
        output.mkdir()
        try:
            # The completion manifest is moved last. Remove everything on failure.
            for artifact in sorted(stage.iterdir(), key=lambda f: (f.name == 'layout.json', f.name)):
                shutil.move(str(artifact), str(output / artifact.name))
        except BaseException:
            shutil.rmtree(output)
            raise
    return manifest
