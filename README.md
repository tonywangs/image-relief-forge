# Image Relief Forge

Turn a PNG or JPEG into a dimensioned relief tile or flat lithophane, entirely
offline after installation. Each conversion creates a closed binary STL, a
grayscale height preview, and a JSON validation report. Optional tiling creates
individually closed meshes, a manifest, and an offline assembly map. No accounts, inference,
image uploads, or external executables are used.

Brightness-based relief **does not recover an object's 3D geometry**. This is a
rectangular thickness map with a flat bottom. Geometry checks **do not establish
physical print quality**; no physical prints have been validated.

## Install and try it

Python 3.10+ is required; the verification environment is Python 3.12 on Linux.
From a checkout, with Python's `venv` and `pip` available:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/relief-forge convert fixtures/orientation.png --output outputs/orientation \
  --width 80 --base 1 --relief 3 --resolution 80
.venv/bin/relief-forge convert fixtures/gradient.jpg --output outputs/lithophane \
  --mode lithophane --width 100 --base 0.8 --relief 2.4 --resolution 128
.venv/bin/relief-forge validate outputs/lithophane/model.stl
```

On Windows, use `.venv\Scripts\python.exe` and `.venv\Scripts\relief-forge.exe`.
You can also invoke the CLI as `python -m image_relief_forge`. The included
[fixtures](fixtures/README.md) are clearly labeled synthetic images, not photos.
Choose a **new output directory** for each conversion: existing paths are never
overwritten. The CLI returns 0 for success, 1 for a failed STL validation, and 2
for invalid arguments, unreadable input, conversion validation failure, or I/O
errors. Validation errors do not emit a successful conversion bundle.

## Optional 3MF

Add `--3mf` after installing `.[three-mf]` to retain the STLs and also produce a
named, millimeter-unit 3MF. It is independently read with consortium lib3mf
before the bundle is accepted. Tiled packages preserve **assembly positions,
not a print-bed arrangement**; the assembled extent can exceed tile build bounds.
See [the format, examples, limits and one-command verification](docs/3mf.md).
No slicer interoperability or physical printing is claimed.

## Controls and units

| Option | Default | Meaning / range |
| --- | --- | --- |
| `--width` | 100 | X extent in millimeters, 0.1–2000 |
| `--base` | 1 | Minimum configured thickness, 0.01–100 mm; strictly positive |
| `--relief` | 3 | Additional thickness range, 0–100 mm; zero makes a flat tile |
| `--resolution` | 128 | Number of grid vertices along the longest image axis, 2–256 |
| `--mode` | relief | `relief`: white is tall; `lithophane`: black is thick |
| `--invert` | off | Reverse the selected mode's brightness mapping |
| `--max-tile-width` | unset | Maximum local X extent per tile, greater than 0 and at most 2000 mm |
| `--max-tile-height` | unset | Maximum local Y extent per tile, greater than 0 and at most 2000 mm; requires both tile options |

The Y extent is `width × oriented_image_height / oriented_image_width`, also
restricted to 0.1–2000 mm. Aspect ratio is preserved exactly before float32 STL
rounding; choosing a coarse grid does not change physical bounds. STL has no unit
metadata: **import the STL into a slicer as millimeters**.

For grayscale brightness `b` between 0 and 1, relief height is
`z = base + relief × b`; lithophane height is
`z = base + relief × (1 − b)`. `--invert` swaps these formulas. The bottom is
always Z=0. If the image lacks an endpoint brightness, the actual thickness range
need not reach the configured minimum or maximum. There is no automatic contrast
normalization, gamma correction, or quantization into printer layers.

Image left-to-right maps to increasing X. The image top is at maximum Y, so
looking down from +Z with +Y pointing up preserves image orientation. JPEG/PNG
EXIF orientation is applied first. Alpha is composited over **white**, then
Pillow's 8-bit grayscale conversion is applied. Embedded ICC profiles are not
applied; brightness uses encoded RGB luma rather than linear-light luminance.
Palette transparency is supported. Grayscale images deeper than 8 bits are
rejected; prepare an 8-bit RGB image when precise source interpretation matters.
Animated PNGs and formats other than PNG/JPEG are rejected.

The shorter grid axis is rounded in proportion to the image dimensions, with at
least two vertices. Both axes span the entire physical rectangle. Bilinear
resampling can smooth small features; high resolutions can upsample the source
without adding information. Single-pixel axes are supported. Example extreme
aspect ratios:

```sh
.venv/bin/relief-forge convert fixtures/wide.png --width 100 --output outputs/wide
.venv/bin/relief-forge convert fixtures/tall.png --width 0.1 --output outputs/tall
```

## Tiled reliefs and lithophanes

Supply both build bounds to opt into tiled output. Without them, the original
single-mesh bundle and STL bytes are preserved. These examples also run in the
isolated offline installation check:

```sh
.venv/bin/relief-forge convert fixtures/orientation.png --output outputs/tiled-relief \
  --width 80 --base 0.8 --relief 2.4 --resolution 64 \
  --max-tile-width 30 --max-tile-height 25
.venv/bin/relief-forge convert fixtures/gradient.jpg --output outputs/tiled-lithophane \
  --width 80 --base 0.8 --relief 2.4 --resolution 64 --mode lithophane \
  --max-tile-width 30 --max-tile-height 25
.venv/bin/relief-forge validate-assembly outputs/tiled-lithophane
```

Open `assembly.svg` in a browser or SVG viewer, offline. It embeds the overall
preview and shows tile numbers, a filename key, grid extents, dimensions, and
assembly origins. The diagram is a top view, with +X right and +Y up. The key
also identifies narrow tiles whose numbers would overlap on the diagram.
Import each STL in millimeters with its flat bottom at Z=0. **No rotation or
axis swapping is used to fit the bounds**: width means X and height means Y,
not thickness. Tiles start at local X=Y=0. Reconstruct the relief by translating
each tile by its manifest `assembly_origin_mm`; rows start at the bottom (+Y
increases with row number) and columns start at the left. Do not mirror tiles.

The image is decoded, oriented, composited and resampled **once globally**.
Partitioning never changes the grid or resamples a crop. Cuts occur only on
sample grid lines. Along each axis, greedily take as many whole cells as fit the
bound, starting at the low coordinate; a shorter remainder ends at the high
coordinate. Float64 ratio noise up to `1e-12` cells is absorbed when deciding an
exact boundary. This is deterministic but may leave unused build area and
unequal edge tiles. It does not minimize seams or balance tile sizes. Shared
boundary samples are included in both neighbors, and each tile gets its own
bottom and side walls; adjoining tile interiors do not overlap.

At most **256 tiles** are allowed. A bound smaller than one sampled cell fails
with an actionable error: increase resolution (up to 256), decrease model width,
or increase the bound. A layout exceeding the tile count also fails before any
STL is written. Even bounds larger than the whole model produce a tiled bundle
with one tile when both options are supplied. Errors leave no output bundle.

A tiled bundle contains:

* `tile-rNNN-cNNN.stl`: each closed tile, in local coordinates; rows and columns
  are one-based. There is no redundant full-size `model.stl` in this bundle.
* `manifest.json` (schema 1): units, global grid and dimensions, requested bounds,
  orientation and partition rules, layout, and every tile's inclusive grid vertex
  extent, assembly translation, physical dimensions including maximum thickness,
  filename, and SHA-256. Grid Y increases upward, unlike image array rows.
* `surface.npy`: the original globally sampled physical heights, little-endian
  float64 in image row order, hashed by the manifest. This reference preserves
  heights before STL float32 rounding and permits independent reconstruction.
* `height.png` and self-contained `assembly.svg`: overall preview and assembly key.
* `report.json` (schema 2): input/mapping/parameters and all per-tile and assembly
  validation results. The single-mesh report remains schema 1.

`validate-assembly` reloads the manifest, reference grid, and every binary STL;
it does not trust previously saved validation results. It checks hashes,
watertightness, winding, volume, nondegenerate triangles and local dimensions,
then checks actual top triangle vertices/connectivity in assembly coordinates.
Every specified cell triangle must occur exactly once. Shared vertices must
agree, and the assembled surface and summed volumes must match the global
untiled piecewise-linear reference. Tests additionally compare with an actual
untiled STL loaded through Trimesh. No repair, rounding weld, or mesh cleanup is
performed; nearest grid indices only identify candidates, whose original
coordinates are separately checked.

Local bounds use relative tolerance `1e-6` and absolute tolerance `1e-7` mm.
Requested build maxima allow `1e-7 + 1e-6 × requested_bound` mm of serialization
error. Surface and seam coordinates allow `1e-7 + 1e-6 × global_axis_extent` mm
per axis (Z uses maximum height). Volume uses relative tolerance `1e-6` and
absolute tolerance `1e-9` mm³. Exact shared source heights serialize identically;
local float32 XY coordinates plus float64 translations can leave tiny numerical
XY discrepancies. Measured errors appear in the report.

These tolerances describe numerical geometry, **not printer clearance**. The
manifest/reference hashes detect inconsistent bundle edits, not authenticity or
agreement with an unavailable source image. `validate-assembly` checks against
the bundled height field; rerun conversion with the source to establish source
provenance. Validation proves neither physical fit, successful printing,
invisible seams, nor optical quality. Connectors, frames, adhesives, assembly
gaps, printer compensation, bed margins, Z capacity and printing orientation
optimization are outside this tool's scope.

## Single-mesh output bundle

* `model.stl`: binary, outward-wound triangles with stored unit normals. Fixed
  header, triangle order, and float32 coordinates; no timestamps.
* `height.png`: the sampled height map in image orientation. Black corresponds
  to the configured base; white to base plus relief. In lithophane mode this is
  the **thickness preview**, not a simulation of transmitted light. When relief
  is zero, all shades map to the same physical height.
* `report.json`: schema version, input SHA-256, dependency versions, complete
  parameters, oriented input size, grid and spacing, brightness formula,
  expected bounds and volume, actual dimensions and volume, triangle count,
  STL SHA-256, and each validation outcome. No source path or timestamp is saved.

Identical inputs and parameters produce identical STL bytes in the tested
environment. Pin dependencies with `requirements-dev.txt` for reproduction;
cross-version JPEG decoder/resampler behavior is not promised. The report also
changes when dependency versions change.

## Geometry checks

A top and bottom grid share a counterclockwise boundary closed by side walls.
The top is a single-valued height function above a strictly positive base, so
this construction has no internal surface intersections. Each grid cell uses
the same diagonal. The report's reference volume integrates these linear
triangles before STL serialization.

The validator opens the **written STL**, checks its byte length, and parses the
coordinates independently of the mesh builder. Exact repeated coordinates are
indexed for topology analysis; it does not round, tolerance-weld, remove faces,
fill holes, or change normals. Checks cover:

* Finite coordinates and stored normals; every edge incident to exactly two faces.
* Opposite directed-edge winding; no duplicate or zero-area triangles.
* Positive signed volume; Euler characteristic of two; matching stored normals.
* During conversion, bounds and volume agreeing with pre-export expectations
  (relative tolerance `1e-6`, absolute bounds tolerance `1e-7` mm and volume
  tolerance `1e-9` mm³).

`validate` alone has no source image or intended dimensions, so it reports actual
bounds and volume without comparing them to user intent. It is not a general
self-intersection detector or a proof that an arbitrary STL is one printable
solid. Tests also load the exported bytes with **Trimesh**, disable its automatic
processing, index only exactly equal vertices, and independently check topology,
winding, area, bounds, and volume.

## Resource limits and limitations

Inputs are limited to 32 MiB compressed and 20 million decoded pixels. The maximum
grid is 256 × 256, producing at most 262,140 triangles and 13,107,084 STL bytes.
The implementation holds arrays in memory; peak memory is larger than the STL
size, so constrained machines should choose a lower resolution. These limits are
fixed safeguards, not configurable promises about available RAM. Tiling adds
side walls: the bounded 255-strip workload produces 521,220 triangles across
255 files. Assembly validation caps total input at 600,000 triangles, 256 tiles,
a 2 MiB manifest and a 256 × 256 float64 reference; each STL retains the original
262,140-triangle limit. See the measured workloads below. STL float32
precision is checked after export. This tool does not add frames, curvature,
supports, printer profiles, or color-material separation.

Physical quality, light transmission, minimum printable wall size, printer bed
fit, and material behavior need separate slicer and hardware checks. A successful
report certifies only the listed geometry checks.

## Reproduce validation and an offline installation

For a tested development environment (Python 3.12):

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m pytest -q
.venv/bin/python -m build --no-isolation
```

Prepare a wheelhouse while connected, on the same Python/platform as the offline
machine. This downloads only public Python packages:

```sh
.venv/bin/python -m pip wheel --no-build-isolation -c requirements-dev.txt --wheel-dir wheelhouse .
```

Copy the wheelhouse and fixtures to the offline machine. Install into a fresh
environment with `python -m pip install --no-index --find-links wheelhouse
image-relief-forge==0.1.0`, then use the CLI normally. No network access is needed
for conversion. The wheel does not include the fixture images: copy `fixtures/`
separately or use your own images.

The automated installation check uses a fresh temporary virtual environment,
installs the wheel with `--no-index`, runs outside the source checkout, disables
Python socket access during conversion, converts four fixtures twice, and checks
identical STL bytes. It also converts the two tiled examples twice, revalidates
every tile and both assemblies, and compares every artifact byte:

```sh
.venv/bin/python scripts/verify_install.py --wheelhouse wheelhouse
```

Run bounded seeded workloads with `.venv/bin/python scripts/benchmark_tiling.py`.
See [validation evidence](docs/validation.md) and [recorded measurements](results/tiling-benchmark.json). The socket guard is a test hook,
not an operating-system sandbox. Wheelhouse files are platform-dependent build
artifacts and are not checked into the source repository.

## Related work and design references

Image-to-lithophane conversion is established work. For example,
[uijincho/lithophane](https://github.com/uijincho/lithophane) provides configurable
brightness-based thickness and STL output. This project makes no novelty claim;
its scope is a small installable CLI with deterministic bundles and explicit
verification. No implementation code was copied from that project.

[Pillow's image API](https://pillow.readthedocs.io/en/stable/reference/Image.html)
documents image decoding and decompression limits, and
[ImageOps](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html)
documents EXIF transposition.
[Trimesh's format documentation](https://trimesh.org/formats.html) explains why
STL triangle soups normally need vertex indexing; this project's independent
tests avoid its default processing so geometry is not silently repaired.

For related established approaches, [antirez/pngtostl](https://github.com/antirez/pngtostl)
describes a separate box per pixel, while [LumaLayer](https://github.com/RonenGru/LumaLayer)
advertises modular grid slicing. The [Image-2-STL lithophane guide](https://www.image-2-stl.com/lithophane-maker/)
suggests splitting a picture in an image editor. This implementation instead
partitions one sampled triangular height field and preserves shared boundary
samples, with a reloadable reference and reconstruction checks. These primary
project pages were reviewed on 2026-09-23; their features and physical claims
were not independently benchmarked. No implementation code was copied, and
neither image-to-STL conversion nor tiling is claimed as novel.
