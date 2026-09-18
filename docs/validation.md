# Validation evidence

Verified with Python 3.12.3 on Linux, NumPy 2.5.3, Pillow 12.3.0, pytest 9.1.1,
and Trimesh 4.12.2. Exact development dependencies are in
[`requirements-dev.txt`](../requirements-dev.txt). These are software geometry
checks; physical print quality and optical performance have not been tested.

## Test suite

```sh
.venv/bin/python -m pytest -q
```

Observed result: **70 passed**. The suite covers independent Trimesh reloads in
both modes, analytic constant-tile volumes, gradients in PNG/JPEG, exact corner
orientation, EXIF rotation, transparent and palette inputs, single-pixel axes,
1000:1 aspect ratios, zero relief, the full 256 × 256 grid, deterministic bundles,
invalid dimensions, source-size limits, corruption and CRC errors, rejected
formats, no-overwrite behavior, and cleanup after a failed validation.

Negative validation cases intentionally remove a face, reverse a face, reverse
the entire mesh, collapse a triangle, inject NaN, append bytes, corrupt normals,
translate the bounds, or duplicate a face. Every damaged case is rejected. A
fully inward mesh remains watertight and consistently wound but correctly fails
the positive-volume check.

The maximum-grid test exports **262,140 triangles**, checks its 10,000 mm³ flat
tile volume, and independently checks watertightness with Trimesh. All fixtures
are synthetic; generation uses no randomness or external data.

## Build and installed CLI

```sh
.venv/bin/python -m build --no-isolation
.venv/bin/python -m pip check
.venv/bin/python -m pip wheel --no-build-isolation -c requirements-dev.txt --wheel-dir wheelhouse .
.venv/bin/python scripts/verify_install.py --wheelhouse wheelhouse
```

The source distribution and wheel built successfully; `pip check` found no
broken requirements. The installation script created an empty virtual
environment and installed only local wheels with `--no-index`. The CLI ran
outside the source tree with Python socket access disabled. It completed eight
conversions (two per input), generated previews and reports, revalidated each
STL through the installed CLI, and found identical repeated STL bytes.

All four installation cases used width 80 mm, base 0.8 mm, relief 2.4 mm, and
resolution 64. The observed hashes are below; changing the input, parameters,
dependency versions, or implementation can change them.

| Synthetic input | Mode | Triangles | STL SHA-256 |
| --- | --- | ---: | --- |
| `gradient.png` | relief | 8,188 | `21d6c8baf03b0b47d7fda131225c536e791eb749c9515d38e2b9eb46d289c00d` |
| `gradient.jpg` | lithophane | 8,188 | `0d4ed6a58ee741404b9aa5419d9ea11d1773081b0afcafcca42d223c5381475f` |
| `orientation.png` | relief | 9,724 | `65d870acf42024c4a324f1b0c0c3329acf6be38a6b4c39b667ea28c6e78bf594` |
| `transparent.png` | lithophane | 8,188 | `6fd5dccdda63f3f3a7557bb9be493e069dc5bdbf47d7dedf33627dedb827153b` |

The socket guard covers Python socket operations, not arbitrary native-code
network activity. The current conversion pipeline uses only local files,
Pillow, and NumPy. macOS, Windows, other Python/dependency versions, general STL
self-intersections, and physical printing remain unverified.
