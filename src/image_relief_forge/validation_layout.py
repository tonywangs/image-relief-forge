"""Read bed packages through lib3mf; validate actual transformed geometry.

This module never calls the packing heuristic or the exporter's transform code.
"""
import json
from pathlib import Path

import numpy as np

from .assembly import _array, _file, validate_assembly
from .three_mf import MAX_TRIANGLES, read_stl, source_entries
from .validation_3mf import preflight


def volume(triangles):
    # Center before integrating, avoiding translation-dependent cancellation.
    t = triangles - triangles.reshape(-1, 3).mean(axis=0)
    return float(np.einsum('ij,ij->i', t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6)


def validate_layout(directory):
    try:
        return _validate(Path(directory))
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        raise ValueError(f'invalid layout bundle: {exc}') from exc


def _validate(directory):
    from .layout import LayoutParameters, digest
    try:
        import lib3mf
    except ImportError as exc:
        raise ValueError('layout requires the three-mf extra (lib3mf==2.5.0)') from exc
    manifest = json.loads(_file(directory, 'layout.json', 2 * 1024 * 1024).read_text())
    if manifest['schema_version'] != 1 or manifest['units'] != 'mm' or manifest['algorithm'] != 'first-fit-fixed-shelves-v1':
        raise ValueError('unsupported layout schema, units or algorithm')
    p = LayoutParameters(**manifest['parameters'])
    p.validate()
    beds, placements = manifest['beds'], manifest['placements']
    if not isinstance(beds, list) or not 1 <= len(beds) <= p.max_beds or manifest['bed_count'] != len(beds):
        raise ValueError('invalid bed count')
    if not isinstance(placements, list) or not 1 <= len(placements) <= p.max_tiles or manifest['tile_count'] != len(placements):
        raise ValueError('invalid tile count')
    source = directory / 'source'
    if source.is_symlink() or not source.is_dir():
        raise ValueError('invalid source snapshot directory')
    entries = source_entries(source)
    reference = {name: (filename, np.asarray(origin, dtype=np.float64)) for name, filename, origin in entries}
    assembly = validate_assembly(source)
    by_id = {}
    for placement in placements:
        name = placement['id']
        if name not in reference or name in by_id or type(placement['bed']) is not int or not 1 <= placement['bed'] <= len(beds):
            raise ValueError('unknown, duplicated or unassigned placement identity')
        if placement['rotation_degrees'] not in ((0, 90) if p.rotate else (0,)):
            raise ValueError('rotation is not allowed by parameters')
        by_id[name] = placement
    if set(by_id) != set(reference):
        raise ValueError('not every source tile is placed exactly once')
    checks = dict(source_assembly=assembly['passed'], source_manifest_hash=digest(source / 'manifest.json') == manifest['source_manifest_sha256'],
                  strict_reader_without_warnings=True, millimeter_units=True, object_counts=True,
                  identities=True, source_hashes=True, artifact_hashes=True, source_triangles=True,
                  rigid_transforms=True, manifest_transforms=True, footprint_metadata=True,
                  assembly_reconstruction=True, unchanged_dimensions=True, unchanged_volume=True,
                  bed_containment=True, bed_contact=True, pairwise_clearance=True,
                  lib3mf_manifold_oriented=True, exactly_once=True, utilization=True)
    wrapper = lib3mf.get_wrapper()
    seen, results = set(), []
    total_triangles = 0
    # lib3mf exposes float32 translations; tolerance scales with bounded bed size.
    tolerance = 1e-7 + 1e-6 * max(p.bed_width, p.bed_height)
    for index, bed in enumerate(beds, 1):
        filename, svg = f'bed-{index:03d}.3mf', f'bed-{index:03d}.svg'
        if bed['id'] != index or bed['file'] != filename or bed['preview'] != svg:
            raise ValueError('invalid bed file identity')
        path = _file(directory, filename, 64 * 1024 * 1024)
        checks['artifact_hashes'] &= digest(path) == bed['sha256'] and digest(_file(directory, svg, 1024 * 1024)) == bed['preview_sha256']
        preflight(path, layout=True)
        model = wrapper.CreateModel()
        reader = model.QueryReader('3mf')
        reader.SetStrictModeActive(True)
        try:
            reader.ReadFromFile(str(path.resolve()))
        except lib3mf.ELib3MFException as exc:
            raise ValueError(f'lib3mf rejected bed package: {exc}') from exc
        checks['strict_reader_without_warnings'] &= reader.GetWarningCount() == 0
        checks['millimeter_units'] &= model.GetUnit() == lib3mf.ModelUnit.MilliMeter
        expected_count = sum(t['bed'] == index for t in placements)
        checks['object_counts'] &= (0 < expected_count == bed['tile_count'] == model.GetObjects().Count() == model.GetMeshObjects().Count() == model.GetBuildItems().Count())
        items = model.GetBuildItems()
        boxes, meshes = [], []
        while items.MoveNext():
            item = items.GetCurrent()
            obj = item.GetObjectResource()
            if not obj.IsMeshObject():
                raise ValueError('bed build item must be a mesh')
            mesh = model.GetMeshObjectByID(item.GetObjectResourceID())
            name = mesh.GetName()
            if name not in by_id or name in seen:
                raise ValueError('unknown or duplicate exported tile')
            seen.add(name)
            placement = by_id[name]
            checks['identities'] &= placement['bed'] == index and mesh.GetPartNumber() == name
            filename, origin = reference[name]
            checks['source_hashes'] &= placement['source_file'] == filename and digest(source / filename) == placement['source_sha256']
            src = read_stl(source / filename).astype(np.float64)
            vertices = np.array([list(v.Coordinates) for v in mesh.GetVertices()], dtype=np.float64)
            faces = np.array([list(t.Indices) for t in mesh.GetTriangleIndices()], dtype=np.int64)
            total_triangles += len(faces)
            if total_triangles > MAX_TRIANGLES:
                raise ValueError('layout total triangle limit exceeded')
            triangles = vertices[faces]
            checks['source_triangles'] &= triangles.shape == src.shape and np.array_equal(triangles, src)
            checks['lib3mf_manifold_oriented'] &= mesh.IsManifoldAndOriented()
            transform = np.array([list(row) for row in item.GetObjectTransform().Fields], dtype=np.float64)
            rotation, translation = transform[:3], transform[3]
            # Independently derive the declared quarter-turn from its angle.
            angle = placement['rotation_degrees']
            expected_rotation = np.array([[0., 1., 0.], [-1., 0., 0.], [0., 0., 1.]]) if angle == 90 else np.eye(3)
            checks['rigid_transforms'] &= np.array_equal(rotation, expected_rotation) and np.linalg.det(rotation) == 1
            declared = _array(placement['local_to_bed'], (4, 3), 'local_to_bed')
            inverse = _array(placement['bed_to_assembly'], (4, 3), 'bed_to_assembly')
            checks['manifest_transforms'] &= np.allclose(transform, declared, atol=tolerance, rtol=0)
            actual = triangles @ rotation + translation
            bounds = np.array([actual.min(axis=(0, 1)), actual.max(axis=(0, 1))])
            local_bounds = np.array([src.min(axis=(0, 1)), src.max(axis=(0, 1))])
            expected_dimensions = np.ptp(local_bounds, axis=0)[[1, 0, 2] if angle else [0, 1, 2]]
            checks['unchanged_dimensions'] &= np.allclose(bounds[1] - bounds[0], expected_dimensions, atol=tolerance, rtol=0)
            checks['footprint_metadata'] &= np.allclose(bounds[0, :2], _array(placement['lower_left_mm'], (2,), 'lower_left'), atol=tolerance, rtol=0) and np.allclose(bounds[1, :2] - bounds[0, :2], _array(placement['footprint_mm'], (2,), 'footprint'), atol=tolerance, rtol=0)
            checks['bed_containment'] &= bool(np.all(bounds[0, :2] >= p.margin - tolerance) and np.all(bounds[1, :2] <= np.array([p.bed_width, p.bed_height]) - p.margin + tolerance))
            checks['bed_contact'] &= abs(bounds[0, 2]) <= tolerance
            actual_volume, source_volume = volume(actual), volume(src)
            checks['unchanged_volume'] &= source_volume > 0 and np.isclose(actual_volume, source_volume, rtol=1e-6, atol=1e-9)
            recovered = actual @ inverse[:3] + inverse[3]
            checks['assembly_reconstruction'] &= recovered.shape == src.shape and np.allclose(recovered, src + origin, rtol=1e-6, atol=tolerance)
            # Also prove the manifest inverse is rigid and is the true inverse.
            checks['assembly_reconstruction'] &= np.allclose(rotation @ inverse[:3], np.eye(3), atol=1e-12, rtol=0) and np.allclose(translation @ inverse[:3] + inverse[3], origin, atol=tolerance, rtol=0)
            boxes.append(bounds)
            meshes.append(dict(id=name, bounds_mm=bounds.tolist(), volume_mm3=actual_volume))
        for a_index, a in enumerate(boxes):
            for b in boxes[a_index + 1:]:
                # Axis-separating rectangular clearance, not center distance.
                gaps = np.maximum(b[0, :2] - a[1, :2], a[0, :2] - b[1, :2])
                checks['pairwise_clearance'] &= bool(np.max(gaps) >= p.clearance - tolerance)
        area = sum(float(np.prod(b[1, :2] - b[0, :2])) for b in boxes)
        checks['utilization'] &= np.isclose(bed['utilization'], area / (p.bed_width*p.bed_height), rtol=1e-6, atol=1e-9) and np.isclose(bed['usable_utilization'], area / ((p.bed_width-2*p.margin)*(p.bed_height-2*p.margin)), rtol=1e-6, atol=1e-9)
        results.append(dict(bed=index, meshes=meshes))
    checks['exactly_once'] = seen == set(reference)
    return dict(passed=all(checks.values()), checks={k: bool(v) for k, v in checks.items()},
                lib3mf_version=list(wrapper.GetLibraryVersion()), coordinate_tolerance_mm=tolerance,
                bed_count=len(beds), tile_count=len(placements), beds=results,
                scope='Strict lib3mf, no repairs; rectangular geometric clearance only. No slicer or physical validation.')
