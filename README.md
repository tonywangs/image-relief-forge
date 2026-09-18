# Image Relief Forge

Turn a PNG or JPEG into a dimensioned relief tile or flat lithophane, entirely
offline after installation. Each conversion creates a closed binary STL, a
grayscale height preview, and a JSON validation report. No accounts, inference,
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

## Controls and units

| Option | Default | Meaning / range |
| --- | --- | --- |
| `--width` | 100 | X extent in millimeters, 0.1–2000 |
| `--base` | 1 | Minimum configured thickness, 0.01–100 mm; strictly positive |
| `--relief` | 3 | Additional thickness range, 0–100 mm; zero makes a flat tile |
| `--resolution` | 128 | Number of grid vertices along the longest image axis, 2–256 |
| `--mode` | relief | `relief`: white is tall; `lithophane`: black is thick |
| `--invert` | off | Reverse the selected mode's brightness mapping |

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

## Output bundle

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
fixed safeguards, not configurable promises about available RAM. STL float32
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
identical STL bytes:

```sh
.venv/bin/python scripts/verify_install.py --wheelhouse wheelhouse
```

See [validation evidence](docs/validation.md). The socket guard is a test hook,
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
