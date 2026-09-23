# Validation evidence

Verified with Python 3.12.3 on Linux, NumPy 2.5.3, Pillow 12.3.0, pytest 9.1.1,
and Trimesh 4.12.2. Exact development dependencies are in
[`requirements-dev.txt`](../requirements-dev.txt). These are software geometry
checks; physical print quality and optical performance have not been tested.

## Test suite

```sh
.venv/bin/python -m pytest -q
```

Observed result: **137 passed** (52.29 seconds on the shared verification host).
Plain pytest output is saved in [`results/tests.log`](../results/tests.log). The suite covers independent Trimesh reloads in
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
single-mesh conversions (two per input), plus four tiled conversions (two per
example). It generated previews, reports and assembly maps, revalidated each
STL and both assemblies through the installed CLI, and found identical repeated
STL bytes and identical bytes for every tiled-bundle artifact.

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

## Tiled geometry and negative cases

The tiled tests reload all STLs through Trimesh with `process=False`, index exact
coordinates only, and compare assembled top triangle connectivity, vertices,
heights and total volume with a separately exported untiled STL. Coverage includes
both modes; asymmetric four-corner markers; PNG/JPEG gradients; black, gray and
white constants; alpha compositing; both extreme-aspect fixtures; uneven grid
divisions; exact build-boundary dimensions; one-tile and 256-tile layouts; zero
relief; and analytic constant volumes under all mode/invert combinations.
Four pinned-environment golden hashes confirm unchanged historical single-mesh
STL bytes. The original 70 regressions still run.

Damaged-bundle tests reject removed tiles, overlapping placements, altered
translations and dimensions, too-small build limits, incorrect hashes and changed
heights. A height edit with an updated STL hash and regenerated normals still
fails the surface/seam checks. Changing a cell diagonal leaves a valid closed
solid and matching vertex positions, but correctly fails the expected surface
connectivity/coverage checks. Rejected inputs also include malformed manifests,
wrong schema/counts, oversized reference-grid headers, duplicate filenames,
path traversal names and symlinked tile files. Invalid tile settings and layouts
leave no published output directory, including when final assembly validation
fails. The validator does not rely on a previously saved `passed` result.

The two documented installed examples each produced six tiles:

| Input / mode | Total triangles | Manifest SHA-256 |
| --- | ---: | --- |
| `orientation.png` / relief | 10,272 | `c8d47f4b0cf8abca87028796f36000c136c91cfa504ae027670447cbc29e7a78` |
| `gradient.jpg` / lithophane | 8,688 | `3d141807c4cf53d8cbb5736c133542148da2dd8f919d79cc660bc42e4a9f8303` |

The installation uses the README parameters: width 80 mm, base 0.8 mm, relief
2.4 mm, resolution 64, maximum tile width 30 mm and height 25 mm. Every local
STL passed geometry validation; every assembly passed bounds, hashes, seam,
coverage, surface and volume checks. All artifacts were byte-identical on repeat.
SVG tests parse the XML, check embedded preview data and verify that the key
contains every tile ID. Browser-specific visual rendering is not verified.

## Bounded synthetic measurements

```sh
.venv/bin/python scripts/benchmark_tiling.py
```

[`results/tiling-benchmark.json`](../results/tiling-benchmark.json) contains actual
measurements, input settings, seed 173, dependency versions and geometry results.
Each workload generates a deterministic random RGBA image locally and launches
in a fresh process. Conversion wall time includes sampling, export, independent
STL validation and assembly reconstruction. Peak resident memory comes from
`resource.getrusage(RUSAGE_SELF)` and includes interpreter imports and fixture
generation. There is no inference, private input or external dataset.

| Workload | Tiles | Triangles | Runtime (s) | Peak RSS (MiB) | Bundle bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| relief, resolution 64 | 9 | 10,524 | 0.255 | 42.27 | 575,158 |
| lithophane, resolution 128 | 9 | 41,044 | 0.856 | 46.23 | 2,176,450 |
| square, resolution 256 | 9 | 266,220 | 6.026 | 71.45 | 14,011,939 |
| 255 one-cell-wide strips, resolution 256 | 255 | 521,220 | 31.102 | 48.17 | 27,234,177 |

Every workload passed all recorded geometry checks. The largest observed
coordinate errors were approximately `3.59e-6` mm against the unquantized surface
and `2.99e-6` mm across seams in X/Y. Seam Z differences were exactly zero; surface
Z rounding error was at most `1.19e-7` mm. The largest absolute total-volume error
was about `0.00806` mm³ on a volume of about `103985.26` mm³, within the documented
relative tolerance. Exact numbers remain in the JSON.

These are single observations on a shared Linux host, with other verification
processes running during part of the measurements. They are not controlled
performance comparisons, throughput guarantees or hardware sizing guidance.
Only the small results JSON is retained; all large generated meshes live in
temporary directories and are removed. The benchmark's RSS measurement depends
on Unix `resource`; the application itself does not. Physical fit, successful
printing, seam visibility and optical quality have not been tested. Numerical
agreement is not evidence of those properties.
