# Rectangular bed layouts

`relief-forge layout` consumes a **tiled conversion bundle**, snapshots its source
geometry, and writes a new self-contained directory with one Core 3MF per bed,
labeled SVG previews, `layout.json`, and independent `validation.json` evidence.
It works offline once the `three-mf` extra is installed. Existing `convert --3mf`
still exports assembly coordinates; its format and behavior are unchanged.

## Reproducible examples

Install `image-relief-forge[three-mf]` using the setup instructions in the README.
Use new output paths for each invocation:

```sh
relief-forge convert fixtures/orientation.png --output outputs/layout-source-small \
  --width 80 --resolution 16 --max-tile-width 30 --max-tile-height 25
relief-forge layout outputs/layout-source-small --output outputs/one-bed \
  --bed-width 180 --bed-height 180 --margin 3 --clearance 2
relief-forge validate-layout outputs/one-bed

relief-forge convert fixtures/orientation.png --output outputs/layout-source \
  --width 80 --resolution 64 --max-tile-width 30 --max-tile-height 25
relief-forge layout outputs/layout-source --output outputs/multiple-beds \
  --bed-width 65 --bed-height 50 --margin 2 --clearance 1 --rotate
relief-forge validate-layout outputs/multiple-beds
```

Open each `bed-NNN.svg` locally to see the footprint arrangement. The numbered
key preserves tile identities even when a strip is too narrow to label inside.
Arrows show the original tile's +X direction. Previews show geometric footprints,
not simulated printing. `layout.json` reports bed count, rotation, positions and
utilization. `bed-NNN.3mf` contains only that bed's assigned objects. Consumers
may rearrange them: we have not tested slicer import behavior.

## Packing contract

`first-fit-fixed-shelves-v1` is a deliberately simple, bounded heuristic:

1. Measure rectangular footprints from the saved float32 STL vertices, promoted
   to float64. Validate the source assembly against its sampled height field.
2. Sort tiles by descending longest side, then descending area, then ascending
   tile identity. Input enumeration order does not affect ties.
3. Enumerate allowed orientations (0°, and optionally 90° counterclockwise
   around +Z), ordered by ascending height, width, then angle. Reject a tile
   with no orientation that fits individually inside the edge margin.
4. Visit beds in creation order. On each bed try existing shelves bottom to top,
   trying orientations in that order. Shelves have fixed height; append each
   tile at the current shelf's right edge if both width and height fit.
5. Otherwise try a new shelf above that bed's existing shelves. If it does not
   fit, visit the next bed, or open a new bed. Stop at the configured bed limit.

Empty space above shorter shelf members is not reused. No backtracking, shelf
resizing, or optimality proof is attempted. Worst-case placement work is
quadratic in tile count, with at most two orientations. A bed-limit error means
**this heuristic exhausted the limit**, not that no feasible layout exists.
An individual-fit failure is a statement about the two permitted orientations,
not arbitrary-angle packing. We make no novelty claim.

All bed sides must be finite and in 0.1..2000 mm. Margin and clearance must be
finite and in 0..2000 mm, with a strictly positive usable bed interior. Tile and
bed limits default to 256 and can be lowered with `--max-tiles` and `--max-beds`;
both accept integers in 1..256. Existing mesh limits also apply: at most 600,000
source triangles, 262,140 triangles per mesh, 131,072 indexed vertices per mesh,
and 64 MiB per uncompressed 3MF package. There is no polygon nesting or irregular
bed support. Input must be an intact tiled bundle produced by this tool, not
arbitrary STL files or a single untiled conversion.

## Clearance, dimensions and transforms

`--margin M` reserves M mm between every tile's rectangular footprint and each
bed edge. `--clearance C` requires every pair on a bed to be separated by **at
least C mm along X or Y**. This is a conservative axis-separating rule, not a
Euclidean corner-distance packing rule. At zero clearance, edges may touch;
positive-area overlap is forbidden. Clearance does not reserve an extra border
at the bed edge. Utilization is the sum of actual tile footprint areas divided
by full bed area; `usable_utilization` divides by the area after edge margins.
Neither includes clearance area as printed area.

Packing comparisons use float64 with no fit tolerance or rounding slack. STL
quantization can make an intended decimal dimension slightly larger or smaller;
the serialized STL footprint is authoritative. Exact fits refer to those
measured dimensions. Source vertex coordinates are copied unchanged into 3MF
resources. Build transforms contain only a proper 0°/90° rotation and translation,
with Z=0 bed contact. The source image and heights are never resampled by layout.

The version-1 JSON manifest uses millimeters and 4×3 row-vector affine matrices:
`bed_point = local_point @ local_to_bed[:3] + local_to_bed[3]`.
`assembly_point = bed_point @ bed_to_assembly[:3] + bed_to_assembly[3]`.
These matrices include the shift required to keep a rotated rectangle's lower
left at its placement origin. Do not interpret `lower_left_mm` as the affine
translation of a rotated mesh. `source/manifest.json` retains original assembly
origins, identities and height-field provenance; original tile STLs and
`surface.npy` are copied byte for byte.

## Independent validation and failure behavior

`validate-layout` does not rerun the packing heuristic or trust saved validation
results. It bounds package sizes and the supported XML profile before loading
lib3mf 2.5.0 in strict mode. It rejects warnings and never invokes repair. It
checks units, exactly-once identities across beds, original triangle coordinates
and connectivity, manifold orientation, actual transformed bounds, bed contact,
rigid transforms, dimensions, signed volumes, pairwise clearance, utilization,
and manifest agreement. It maps transformed vertices back to the independently
validated source assembly and compares every triangle, including asymmetric
height markers. Hashes cover source tiles, the source manifest, bed packages and
previews. Hashes detect changes, not authenticity; a self-contained bundle is
its own source of provenance, not a signature or an external image attestation.

lib3mf's API exposes float32 transforms, so geometric comparisons allow
`1e-7 + 1e-6 * max(bed_width, bed_height)` mm coordinate error. Volume comparisons
use relative tolerance 1e-6 and absolute tolerance 1e-9 mm³. Assembly comparisons
also allow relative coordinate error 1e-6. These are numerical validation limits,
not extra printable clearance: choose practical margins well above them.

Output paths must be new. Source snapshots, serialization and verification
finish in a temporary sibling directory before the destination is created
exclusively. The completion manifest is moved last. Handled failures remove the
new output and staging directory; a concurrent pre-existing destination is left
alone. Abrupt process/OS termination can leave a partial directory or temporary
files; absence of `layout.json` indicates no completed bundle, and a fresh
`validate-layout` must pass before use. The validator checks this exporter's
bounded profile, not all valid 3MF documents; preview SVG contents are checked
by hash rather than independently rendered by the validator.

## Verification and evidence

With the development environment and offline wheelhouse prepared as in the
README, run one command:

```sh
.venv/bin/python scripts/verify_layout.py --record results/layout-verification.json
```

This retains all previous conversion/tiling/3MF tests, build checks, isolated
installation examples, and bounded 3MF workloads. It adds 200 seeds (0..199),
repeated full-bundle comparisons, uneven tiles and asymmetric height markers,
exact-fit and rotation-only cases, invalid dimensions, exhausted limits,
geometry tampering, collisions and injected serialization/move failures.
`scripts/benchmark_layout.py` then installs the wheel into a fresh temporary
venv with `--no-index`, runs the public CLI outside the checkout with a Python
socket audit guard, and measures four bounded workloads. The first two are the
single-bed and multi-bed examples above; the other two exercise resolution 128
and 256. Each workload repeats export and revalidation and hashes every artifact.
The JSON records actual times, peak CLI process RSS, dependencies, sizes and
hashes. Measurements are shared-host observations, not performance guarantees.
The Linux benchmark runner uses `resource`; cross-platform behavior is untested.

**Unperformed validation:** brims, supports, toolhead clearance, sequential-print
safety, slicer interoperability and physical printing. Bed layouts do not
establish that a printer can safely execute a job. No inference service, private
data, hardware or paid compute is needed for these examples.

## Recorded observations

The [recorded verification](../results/layout-verification.json) on Linux /
Python 3.12.3 passed all 615 tests, the build/dependency checks, historical
installation checks and both benchmark suites. Historical assembly 3MF
package hashes matched their saved baseline. The 2026-09-25 layout run
observed the following (two exports per workload):

| Workload | Tiles | Beds | Layout seconds | Peak CLI RSS (MiB) | Bundle bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| single-bed | 9 | 1 | 0.68–0.69 | 47.78 | 123,621 |
| multi-bed | 6 | 2 | 1.57–1.59 | 51.90 | 1,301,534 |
| dense-128 | 15 | 4 | 4.89–4.91 | 58.97 | 5,282,058 |
| fine-256 | 9 | 2 | 18.74–18.76 | 143.62 | 20,747,522 |

All four full bundles repeated byte for byte and passed fresh validation.
The JSON contains per-artifact hashes and sizes, every geometry check,
dependency versions, conversion times and separate revalidation times.
Peak memory follows Linux [getrusage](https://man7.org/linux/man-pages/man2/getrusage.2.html)
semantics: the largest completed CLI child process across the workload,
not the sum of process memory. These observations do not predict other
machines or establish packing optimality.

## Related implementations and specification

Shelf packing, skyline packing and MaxRects are established approaches.
[Jukka Jylänki's RectangleBinPack](https://github.com/juj/RectangleBinPack) includes
[shelf heuristics](https://github.com/juj/RectangleBinPack/blob/master/ShelfBinPack.cpp)
and [MaxRects](https://github.com/juj/RectangleBinPack/blob/master/MaxRectsBinPack.cpp).
Our fixed-shelf first-fit implementation trades packing efficiency for a short,
deterministic contract; no code was copied and no comparative performance or
packing-quality advantage is claimed. A future improvement could benchmark
heuristics on the same saved tile footprints before changing the versioned
algorithm.

The [3MF Core 1.3.0 specification](https://3mf.io/wp-content/uploads/sites/106/2025/02/3MF_Core_Specification_v1.3.0.pdf)
defines mesh resources, millimeter units and row-vector build transforms.
[lib3mf](https://github.com/3MFConsortium/lib3mf) provides the independent reader.
The new packages use the same mesh-only Core profile as the existing exporter,
with quarter-turn build rotations enabled specifically for layout validation.
