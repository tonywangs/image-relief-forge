"""One offline verification entry point, retaining the previous regression suite."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    commands = [
        [sys.executable, '-m', 'pytest', '-q'],
        [sys.executable, '-m', 'build', '--no-isolation'],
        [sys.executable, '-m', 'pip', 'check'],
        [sys.executable, '-m', 'pip', 'wheel', '--no-index', '--no-build-isolation', '--no-deps', '--wheel-dir', 'wheelhouse', '.'],
        [sys.executable, 'scripts/verify_install.py', '--wheelhouse', 'wheelhouse'],
        [sys.executable, 'scripts/benchmark_3mf.py'],
        [sys.executable, 'scripts/benchmark_layout.py', '--wheelhouse', 'wheelhouse'],
        ['git', 'diff', '--check'],
    ]
    env = dict(os.environ, PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
    checks = []
    for command in commands:
        print('Running:', ' '.join(command), flush=True)
        start = time.perf_counter()
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
        print(result.stdout, end='', flush=True)
        if result.returncode:
            print(result.stderr, file=sys.stderr)
        entry = dict(command=['python' if c == sys.executable else c for c in command],
                     exit_code=result.returncode, elapsed_seconds=time.perf_counter() - start)
        if any(c in command for c in ('scripts/verify_install.py', 'scripts/benchmark_3mf.py', 'scripts/benchmark_layout.py')) and result.returncode == 0:
            entry['result'] = json.loads(result.stdout)
            if 'scripts/benchmark_3mf.py' in command:
                baseline = json.loads((root / 'results/3mf-benchmark.json').read_text())
                expected = {w['case']: w['package_sha256'] for w in baseline['workloads']}
                actual = {w['case']: w['package_sha256'] for w in entry['result']['workloads']}
                if actual != expected:
                    raise AssertionError('historical assembly-coordinate 3MF bytes changed')
                entry['historical_package_hashes_unchanged'] = True
        if '-q' in command:
            entry['test_output'] = result.stdout
        checks.append(entry)
        if args.record:
            args.record.write_text(json.dumps(dict(python=platform.python_version(), platform=platform.platform(),
                checks=checks), indent=2, sort_keys=True) + '\n')
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
