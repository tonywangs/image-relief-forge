# Offline 3MF export and validation

Install `.[three-mf]` to add the consortium's `lib3mf==2.5.0` native reader.
Export is opt-in: append `--3mf` to any `convert` command. It adds `model.3mf`
and an `artifacts.3mf` report entry containing its SHA-256, size, specification
version, mesh count and independent validation. Existing STL, preview, manifest,
surface and map bytes are preserved. Conversion without `--3mf` retains the
historical report and artifact semantics.

```sh
.venv/bin/python -m pip install '.[three-mf]'
.venv/bin/relief-forge convert fixtures/orientation.png --output outputs/3mf-single \
  --width 80 --base 0.8 --relief 2.4 --resolution 64 --3mf
.venv/bin/relief-forge convert fixtures/gradient.jpg --output outputs/3mf-tiled \
  --width 80 --base 0.8 --relief 2.4 --resolution 64 --mode lithophane \
  --max-tile-width 30 --max-tile-height 25 --3mf
.venv/bin/relief-forge validate-3mf outputs/3mf-tiled
```

The **3MF contains assembly positions, not a print-bed arrangement**. The whole
assembled image may exceed either requested tile bound. Tiles touch at their
walls; slicers may merge touching objects, recenter them, or rearrange them.
Use the separate local-coordinate tile STLs for individual printing. This tool
does not perform packing, add gaps or clearances, compensate a printer, or assert
that the assembled package fits a printer. Names identify pieces, not separate
print jobs. No slicer interoperability or successful printing has been tested.

## Pinned format and reproducibility

The implementation targets [3MF Core 1.3.0, pinned source revision
20c079eef39e45ed223b8443dc9f34cbe32dc2c2](https://github.com/3MFConsortium/spec_core/blob/20c079eef39e45ed223b8443dc9f34cbe32dc2c2/3MF%20Core%20Specification.md).
It uses the Core mesh subset with explicit `unit="millimeter"`, one named mesh
object and one build item per STL. The single object is `model`; tile names and
part numbers are `tile-rNNN-cNNN`, ordered bottom row first, then left to right.
IDs are consecutive positive integers. Build transforms are identity plus the
manifest's assembly translation, using the Core 4-by-3 row-major convention.
There are no extensions, materials, components, external resources, thumbnails,
printer settings, or generated timestamps. Core versions use a shared namespace;
there is no invented XML version attribute.

Vertices are lexicographically indexed by exact equality of the existing STL
float32 coordinates; triangle order and winding are preserved. This is an
explicit common geometry contract, not additional high-precision surface output.
Numbers use Python's locale-independent `.17g` formatting, with zero normalized
to `0`; this preserves float64 assembly origins and exactly represents float32
mesh coordinates when read back by lib3mf. ZIP entries are stored uncompressed
in this order: `[Content_Types].xml`, `_rels/.rels`, `3D/3dmodel.model`. Each uses
1980-01-01 00:00:00, Unix regular-file mode 0644, no extra fields or comments,
and no ZIP64. Stored ZIP removes compression-library variability at the cost of
larger files. Reproducibility covers pinned Python/dependency environments;
cross-platform or cross-version identity has not been established.

Limits are 256 meshes/tiles, 131,072 vertices and 262,140 triangles per mesh,
600,000 total triangles, and 64 MiB each for XML and final package. Existing
image/grid/assembly limits also apply. Serialization checks byte limits while
writing bounded memory buffers. The reader preflights package parts, sizes,
structure, coordinates and indices before invoking native code. It only accepts
this exporter profile (including stored ZIP); it is **not a general 3MF validator**.
Memory use exceeds package size; see measured peak RSS. Limits bound work but do
not guarantee success under arbitrary available RAM.

Export and independent validation finish in a temporary sibling directory before
any final bundle appears. An existing output path is refused, including symlinks.
Serialization, resource-limit and validation failures clean up staging. Caught
publication errors remove the partially moved bundle. Process termination or
power loss during the final moves is outside that transactional guarantee.

## Independent reader and geometry checks

Every successful `--3mf` conversion uses the [consortium's lib3mf
implementation](https://github.com/3MFConsortium/lib3mf) via its [official Python
package](https://github.com/3MFConsortium/lib3mf_python). The reader is in strict
mode; any warning fails validation. No repair API is called. Validation checks
units, resource and build counts, names, IDs, part numbers, transforms, native
manifold/orientation results and exact equality of every read triangle with its
source STL. It checks closed topology, consistent winding, nondegenerate faces,
positive signed volume and source bounds using independently re-read geometry.

For tiled packages, read geometry is serialized into temporary STLs without
changing vertex positions or face connectivity. Translations returned by lib3mf
replace manifest origins. The existing assembly validator then independently
maps actual top triangles to the original sampled grid, checks both triangles
of every cell exactly once, shared seam samples, reference heights, dimensions,
local build bounds and integrated total volume. Re-serialization computes STL
normals only; it does not repair topology. This reuses the established assembly
checker, not the exporter or its partitioning algorithm.

lib3mf's public vertex and transform API uses float32. Mesh coordinates must
agree exactly with the float32 source triangles. Transforms and dimensions use
`rtol=1e-6`, `atol=1e-7` mm; assembly seam/surface tolerance is
`1e-7 + 1e-6 * global_axis_extent` mm, and volume uses `rtol=1e-6`,
`atol=1e-9` mm³. These are numerical tolerances, not physical clearances.
The bundle's references are not an authenticity check. A consistent alteration
of all source artifacts requires original-image regeneration to detect.

## Reproduce checks

Prepare the pinned environment and platform wheelhouse while online:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m pip wheel --no-build-isolation -c requirements-dev.txt --wheel-dir wheelhouse '.[three-mf]'
```

Then one offline verification command runs regressions, 200 seeded cases (each
exported twice), malformed/failure cases, build/dependency checks, an isolated
installed CLI exercise, and bounded fresh-process benchmarks:

```sh
.venv/bin/python scripts/verify_3mf.py
```

The seeded tests include both modes, inversion, asymmetric corner markers,
odd image/grid dimensions, RGBA compositing, uneven divisions and extreme
aspects. Separate tests cover 256 tiles, a flat surface, 19,999:1 aspects,
malformed packages, corruption, output collisions, resource limits and injected
I/O/validation failures. Existing STL regressions remain in the same suite.
The installed CLI uses `--no-index` and a Python socket guard; this is not an OS
network sandbox. Fixture PNG/JPEGs must be copied separately from the wheel.

[Full verification evidence](../results/3mf-verification.json) records the build,
dependency, installed CLI and benchmark checks. The [final test result](../results/3mf-tests.json)
records the complete regression suite.

[Recorded measurements](../results/3mf-benchmark.json) contain actual wall times,
peak process memory, versions, byte sizes, hashes, repeatability and validation
outcomes. Measurements are shared-host observations, not performance guarantees.
Historical tiling results are retained separately. Linux/Python 3.12 is the tested
platform; other operating systems, slicers, printers and physical fit are untested.

3MF writing is established functionality in lib3mf and tools such as
[Bambu Studio's 3MF implementation](https://github.com/bambulab/BambuStudio/blob/master/src/libslic3r/Format/3mf.cpp).
The scope here is deterministic mesh-only packaging plus reproducible checks;
no novelty claim or copied implementation code is involved. These primary
sources were reviewed during this milestone.
