# Synthetic fixtures

All images here are generated locally by `scripts/generate_fixtures.py`. They
contain no photographs or private data. Run that script with the locked Pillow
and NumPy versions to reproduce the image bytes.

| Fixture | Purpose |
| --- | --- |
| `black.png`, `gray.png`, `white.png` | Constant-height tiles and analytic volumes |
| `gradient.png`, `gradient.jpg` | Horizontal brightness ramp, PNG/JPEG decoding |
| `orientation.png` | Corners: top-left 0, top-right 64, bottom-left 192, bottom-right 255 |
| `transparent.png` | Opaque black left half, transparent black right half; right becomes white |
| `wide.png`, `tall.png` | 1000:1 and 1:1000 aspect ratios, including single-pixel axes |

Tests create additional EXIF-oriented, animated, corrupt, and unsupported images
in temporary directories. No random seed is needed: fixtures are deterministic.
