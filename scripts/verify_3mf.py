"""One offline milestone verification command; requires prepared wheelhouse."""
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
    parser.add_argument('--record', type=Path, help='write actual check evidence as JSON')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    commands = [
        [sys.executable, '-m', 'pytest', '-q'],
        [sys.executable, '-m', 'build', '--no-isolation'],
        [sys.executable, '-m', 'pip', 'check'],
        [sys.executable, '-m', 'pip', 'wheel', '--no-index', '--no-build-isolation', '--no-deps', '--wheel-dir', 'wheelhouse', '.'],
        [sys.executable, 'scripts/verify_install.py', '--wheelhouse', 'wheelhouse'],
        [sys.executable, 'scripts/benchmark_3mf.py'],
        ['git', 'diff', '--check'],
    ]
    evidence = []
    env = dict(os.environ, PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
    for command in commands:
        print('Running:', ' '.join(command), flush=True)
        start = time.perf_counter()
        result = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True)
        print(result.stdout, end='', flush=True)
        if result.returncode:
            print(result.stderr, file=sys.stderr)
        entry = dict(command=['python' if x == sys.executable else x for x in command],
                     exit_code=result.returncode, elapsed_seconds=time.perf_counter() - start)
        if 'scripts/verify_install.py' in command or 'scripts/benchmark_3mf.py' in command:
            if result.returncode == 0:
                entry['result'] = json.loads(result.stdout)
        elif '-q' in command:
            entry['test_summary'] = result.stdout.strip().splitlines()[-1]
        evidence.append(entry)
        if args.record:
            args.record.write_text(json.dumps(dict(python=platform.python_version(),
                platform=platform.platform(), checks=evidence), indent=2, sort_keys=True) + '\n')
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
