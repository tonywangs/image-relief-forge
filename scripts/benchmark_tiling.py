"""Bounded synthetic tiling workloads; each measurement uses a fresh process.

Linux/macOS only: resource.getrusage supplies process peak resident memory.
No timing or memory measurement is treated as a portable performance guarantee.
"""

import argparse
import json
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import time

CASES = {
    "relief-64": dict(size=(160, 96), width=120, resolution=64, bounds=(45, 30), mode="relief"),
    "lithophane-128": dict(size=(160, 96), width=120, resolution=128, bounds=(45, 30), mode="lithophane"),
    "square-256": dict(size=(256, 256), width=200, resolution=256, bounds=(70, 70), mode="relief"),
    "strips-255": dict(size=(256, 256), width=255, resolution=256, bounds=(1, 255), mode="relief"),
}
SEED = 173


def worker(name):
    import resource
    import numpy as np
    from PIL import Image, __version__ as pillow_version
    from image_relief_forge.conversion import Parameters, convert

    case = CASES[name]
    with tempfile.TemporaryDirectory(prefix="relief-benchmark-") as temporary:
        root = Path(temporary)
        rng = np.random.default_rng(SEED)
        x, y = case["size"]
        # Asymmetric, varying alpha, bounded local synthetic input.
        pixels = rng.integers(0, 256, (y, x, 4), dtype=np.uint8)
        Image.fromarray(pixels).save(root / "source.png")
        params = Parameters(width=case["width"], base=0.8, relief=2.4,
                            resolution=case["resolution"], mode=case["mode"],
                            max_tile_width=case["bounds"][0], max_tile_height=case["bounds"][1])
        start = time.perf_counter()
        report = convert(root / "source.png", root / "out", params)
        elapsed = time.perf_counter() - start
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_bytes = int(peak if sys.platform == "darwin" else peak * 1024)
        files = list((root / "out").iterdir())
        check = report["validation"]
        if not check["passed"]:
            raise RuntimeError("benchmark geometry validation failed")
        return {"case": name, "input": case, "seed": SEED,
                "runtime_seconds": elapsed, "peak_process_rss_bytes": peak_bytes,
                "output_bytes": sum(p.stat().st_size for p in files),
                "stl_bytes": sum(p.stat().st_size for p in files if p.suffix == ".stl"),
                "output_files": len(files), "tiles": check["tile_count"], "triangles": check["triangle_count"],
                "geometry_checks": check["checks"], "volume_mm3": check["volume_mm3"],
                "volume_error_mm3": check["volume_error_mm3"],
                "max_surface_error_mm_xyz": check["max_surface_error_mm_xyz"],
                "max_seam_error_mm_xyz": check["max_seam_error_mm_xyz"],
                "numpy": np.__version__, "pillow": pillow_version}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=CASES)
    parser.add_argument("--output", type=Path, help="optional JSON results file; replaces existing file")
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker), sort_keys=True))
        return
    results = []
    for name in CASES:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", name],
                                   check=True, capture_output=True, text=True)
        results.append(json.loads(completed.stdout))
    report = {"schema_version": 1, "python": platform.python_version(), "platform": platform.platform(),
              "measurement": "convert wall time including export and validation; fresh-process peak RSS including imports and fixture generation",
              "limitations": "single observations; no physical printing, optical evaluation or cross-platform performance claim",
              "workloads": results}
    serialized = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
