"""Bounded 3MF benchmarks, fresh processes, repeated export and strict validation."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

CASES = {
    'single-64': dict(resolution=64, width=80, tiled=False, bounds=(30, 25)),
    'single-256': dict(resolution=256, width=200, tiled=False, bounds=(70, 70)),
    'tiled-128': dict(resolution=128, width=120, tiled=True, bounds=(45, 30)),
    'strips-255': dict(resolution=256, width=255, tiled=True, bounds=(1, 255)),
}


def worker(name):
    import resource
    import numpy as np
    from PIL import Image
    from image_relief_forge.conversion import Parameters, convert
    case = CASES[name]
    with tempfile.TemporaryDirectory(prefix='forge-3mf-benchmark-') as temp:
        root = Path(temp)
        pixels = np.random.default_rng(173).integers(0, 256, (256, 256, 4), dtype=np.uint8)
        Image.fromarray(pixels).save(root / 'input.png')
        params = Parameters(width=case['width'], resolution=case['resolution'], base=.8, relief=2.4,
                            mode='lithophane', max_tile_width=case['bounds'][0] if case['tiled'] else None,
                            max_tile_height=case['bounds'][1] if case['tiled'] else None)
        times, hashes = [], []
        for repeat in range(2):
            out = root / str(repeat)
            start = time.perf_counter()
            report = convert(root / 'input.png', out, params, three_mf=True)
            times.append(time.perf_counter() - start)
            assert report['artifacts']['3mf']['validation']['passed']
            hashes.append(hashlib.sha256((out / 'model.3mf').read_bytes()).hexdigest())
        assert hashes[0] == hashes[1]
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        artifact = report['artifacts']['3mf']
        return dict(case=name, input=case, seed=173, runtime_seconds=times,
                    peak_process_rss_bytes=int(peak if sys.platform == 'darwin' else peak * 1024),
                    package_bytes=artifact['bytes'], package_sha256=hashes[0], repeatable=True,
                    triangles=report['validation']['triangle_count'], meshes=artifact['mesh_count'],
                    bundle_bytes=sum(p.stat().st_size for p in out.iterdir()),
                    checks=artifact['validation']['checks'],
                    assembly=artifact['validation']['assembly'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', choices=CASES)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker)))
        return
    results = []
    for name in CASES:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', name],
                                text=True, capture_output=True, check=True)
        results.append(json.loads(result.stdout))
    report = dict(python=platform.python_version(), platform=platform.platform(),
                  dependencies={p: importlib.metadata.version(p) for p in ('numpy', 'Pillow', 'lib3mf')},
                  measurement='Two sequential conversions per fresh worker; wall time includes all STL/3MF export and validation. Peak RSS covers both conversions, imports and fixture generation.',
                  limitations='Shared-host observations, not performance guarantees. No slicer or physical validation.',
                  workloads=results)
    data = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.write_text(data)
    print(data, end='')


if __name__ == '__main__':
    main()
