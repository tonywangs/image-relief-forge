"""Install a built wheel offline into a fresh venv; exercise the public CLI.

Requires pip in the launching interpreter and a prepared platform wheelhouse.
Does not require ensurepip in the target Python installation.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def run(args, cwd, env):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, text=True,
                            capture_output=True, check=True)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, default=Path("wheelhouse"))
    args = parser.parse_args()
    wheelhouse = args.wheelhouse.resolve()
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONNOUSERSITE="1")
    env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="relief-forge-install-") as temporary:
        work = Path(temporary)
        environment = work / "venv"
        venv.EnvBuilder(with_pip=False).create(environment)
        executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        cli = environment / ("Scripts/relief-forge.exe" if os.name == "nt" else "bin/relief-forge")
        run([sys.executable, "-m", "pip", "--python", executable, "install", "--no-index",
             "--find-links", wheelhouse, "image-relief-forge==0.1.0"], work, env)
        site_packages = Path(run([executable, "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], work, env).strip())
        # Every conversion process aborts on attempted Python socket use. This is
        # a smoke-test guard, not an operating-system network sandbox.
        (site_packages / "sitecustomize.py").write_text(
            "import sys\n"
            "def deny_network(event, args):\n"
            "    if event.startswith('socket.'):\n"
            "        raise RuntimeError('network disabled for offline verification')\n"
            "sys.addaudithook(deny_network)\n", encoding="utf-8")
        installed = Path(run([executable, "-I", "-c", "import image_relief_forge; print(image_relief_forge.__file__)"], work, env).strip())
        assert environment in installed.parents, installed
        results = []
        for fixture, mode in (("gradient.png", "relief"), ("gradient.jpg", "lithophane"),
                              ("orientation.png", "relief"), ("transparent.png", "lithophane")):
            source = root / "fixtures" / fixture
            hashes = []
            for iteration in range(2):
                output = work / f"{fixture}-{iteration}"
                run([cli, "convert", source, "--output", output, "--width", "80", "--base", "0.8",
                     "--relief", "2.4", "--resolution", "64", "--mode", mode], work, env)
                report = json.loads((output / "report.json").read_text())
                assert report["validation"]["passed"]
                assert (output / "height.png").is_file()
                check = json.loads(run([executable, "-I", "-m", "image_relief_forge", "validate", output / "model.stl"], work, env))
                assert check["passed"]
                hashes.append(hashlib.sha256((output / "model.stl").read_bytes()).hexdigest())
            assert hashes[0] == hashes[1]
            results.append({"fixture": fixture, "mode": mode, "stl_sha256": hashes[0],
                            "triangles": report["validation"]["triangle_count"]})
        print(json.dumps({"isolated_install": True, "offline_install": True, "socket_guard": True,
                          "repeated_stl_bytes_identical": True, "conversions": results}, indent=2))


if __name__ == "__main__":
    main()
