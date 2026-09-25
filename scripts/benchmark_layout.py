"""Offline installed-CLI layout examples, repeatability and bounded measurements."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import venv

# Rectangular orientation fixture gives uneven final strips and marked corners.
CASES = {
    'single-bed': dict(width=80, resolution=16, tile=(30, 25), bed=(180, 180), margin=3, clearance=2, rotate=False),
    'multi-bed': dict(width=80, resolution=64, tile=(30, 25), bed=(65, 50), margin=2, clearance=1, rotate=True),
    'dense-128': dict(width=180, resolution=128, tile=(45, 40), bed=(100, 85), margin=3, clearance=2, rotate=True),
    'fine-256': dict(width=200, resolution=256, tile=(70, 60), bed=(150, 130), margin=4, clearance=1.5, rotate=True),
}


def run(command, cwd, env):
    start = time.perf_counter()
    result = subprocess.run([str(a) for a in command], cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{command}: exit {result.returncode}\n{result.stdout}\n{result.stderr}')
    return result.stdout, time.perf_counter() - start


def worker(name, cli, fixture):
    import resource
    case = CASES[name]
    env = dict(os.environ, PIP_NO_INDEX='1', PYTHONNOUSERSITE='1')
    env.pop('PYTHONPATH', None)
    with tempfile.TemporaryDirectory(prefix='forge-layout-bench-') as temporary:
        work = Path(temporary)
        source = work / 'source'
        _, conversion_seconds = run([cli, 'convert', fixture, '--output', source, '--width', case['width'],
            '--resolution', case['resolution'], '--max-tile-width', case['tile'][0],
            '--max-tile-height', case['tile'][1]], work, env)
        times, artifacts, reports = [], [], []
        for repeat in range(2):
            out = work / f'layout-{repeat}'
            args = [cli, 'layout', source, '--output', out, '--bed-width', case['bed'][0],
                    '--bed-height', case['bed'][1], '--margin', case['margin'], '--clearance', case['clearance']]
            if case['rotate']:
                args.append('--rotate')
            _, elapsed = run(args, work, env)
            times.append(elapsed)
            report, validation_seconds = run([cli, 'validate-layout', out], work, env)
            report = json.loads(report)
            assert report['passed']
            reports.append(dict(elapsed_seconds=validation_seconds, checks=report['checks']))
            artifacts.append({str(p.relative_to(out)): dict(bytes=p.stat().st_size,
                sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(out.rglob('*')) if p.is_file()})
        assert artifacts[0] == artifacts[1]
        manifest = json.loads((out / 'layout.json').read_text())
        if name == 'single-bed':
            assert manifest['bed_count'] == 1
        if name == 'multi-bed':
            assert manifest['bed_count'] > 1
        # Check the installed parser/error status and the no-overwrite behavior.
        before = artifacts[-1]
        collision = subprocess.run([str(a) for a in args], cwd=work, env=env, capture_output=True, text=True)
        assert collision.returncode == 2 and 'already exists' in collision.stderr
        assert before == {str(p.relative_to(out)): dict(bytes=p.stat().st_size,
            sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(out.rglob('*')) if p.is_file()}
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        return dict(case=name, parameters=case, fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
                    conversion_seconds=conversion_seconds, layout_seconds=times, revalidation=reports,
                    peak_cli_process_rss_bytes=int(peak if sys.platform == 'darwin' else peak*1024),
                    repeated_bundle_bytes_identical=True, collision_exit_code=collision.returncode,
                    bed_count=manifest['bed_count'], tile_count=manifest['tile_count'],
                    utilization=[b['utilization'] for b in manifest['beds']],
                    bundle_bytes=sum(a['bytes'] for a in artifacts[0].values()), artifacts=artifacts[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse', type=Path, default=Path('wheelhouse'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', choices=CASES)
    parser.add_argument('--cli', type=Path)
    parser.add_argument('--fixture', type=Path)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.cli, args.fixture)))
        return
    root = Path(__file__).resolve().parents[1]
    wheelhouse = args.wheelhouse.resolve()
    env = dict(os.environ, PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONNOUSERSITE='1')
    env.pop('PYTHONPATH', None)
    with tempfile.TemporaryDirectory(prefix='forge-layout-install-') as temporary:
        work = Path(temporary)
        environment = work / 'venv'
        venv.EnvBuilder(with_pip=False).create(environment)
        python = environment / 'bin/python'
        cli = environment / 'bin/relief-forge'
        run([sys.executable, '-m', 'pip', '--python', python, 'install', '--no-index',
             '--find-links', wheelhouse, 'image-relief-forge[three-mf]==0.1.0'], work, env)
        installed, _ = run([python, '-I', '-c', 'import image_relief_forge; print(image_relief_forge.__file__)'], work, env)
        assert environment in Path(installed.strip()).parents
        site, _ = run([python, '-I', '-c', "import sysconfig; print(sysconfig.get_path('purelib'))"], work, env)
        (Path(site.strip()) / 'sitecustomize.py').write_text(
            "import sys\ndef deny(event, args):\n"
            "    if event.startswith('socket.'):\n        raise RuntimeError('offline verification: socket denied')\n"
            "sys.addaudithook(deny)\n")
        dependencies, _ = run([python, '-I', '-c',
            "import json, importlib.metadata as m; print(json.dumps({p:m.version(p) for p in ['numpy','Pillow','lib3mf','image-relief-forge']}))"], work, env)
        workloads = []
        for name in CASES:
            output, _ = run([sys.executable, '-I', Path(__file__).resolve(), '--worker', name,
                            '--cli', cli, '--fixture', root / 'fixtures/orientation.png'], work, env)
            workloads.append(json.loads(output))
        report = dict(python=platform.python_version(), platform=platform.platform(),
                      dependencies=json.loads(dependencies), isolated_install=True, offline_install=True,
                      python_socket_guard=True, workloads=workloads,
                      measurement='Each workload has a fresh worker and isolated installed CLI child processes. Layout wall times include startup, snapshot, packing, serialization and strict validation. Peak RSS is the maximum child process RSS across conversion, two layouts, two revalidations and a collision check, not their sum; Linux ru_maxrss KiB converted to bytes.',
                      limitations='Shared-host observations; Python socket audit guard is not a native network sandbox. Linux-only benchmark runner; no slicer or physical tests.')
        data = json.dumps(report, indent=2, sort_keys=True) + '\n'
        if args.output:
            args.output.write_text(data)
        print(data, end='')


if __name__ == '__main__':
    main()
