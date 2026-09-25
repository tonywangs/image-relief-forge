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
             "--find-links", wheelhouse, "image-relief-forge[three-mf]==0.1.0"], work, env)
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
            mf_hashes = []
            for iteration in range(2):
                output = work / f"{fixture}-{iteration}"
                run([cli, "convert", source, "--output", output, "--width", "80", "--base", "0.8",
                     "--relief", "2.4", "--resolution", "64", "--mode", mode, "--3mf"], work, env)
                report = json.loads((output / "report.json").read_text())
                assert report["validation"]["passed"]
                assert json.loads(run([cli, "validate-3mf", output], work, env))["passed"]
                assert (output / "height.png").is_file()
                check = json.loads(run([executable, "-I", "-m", "image_relief_forge", "validate", output / "model.stl"], work, env))
                assert check["passed"]
                mf_hashes.append(hashlib.sha256((output / "model.3mf").read_bytes()).hexdigest())
                hashes.append(hashlib.sha256((output / "model.stl").read_bytes()).hexdigest())
            assert hashes[0] == hashes[1]
            assert mf_hashes[0] == mf_hashes[1]
            results.append({"fixture": fixture, "mode": mode, "stl_sha256": hashes[0], "3mf_sha256": mf_hashes[0],
                            "triangles": report["validation"]["triangle_count"]})
        tiled_results = []
        for fixture, mode in (("orientation.png", "relief"), ("gradient.jpg", "lithophane")):
            repeated = []
            for iteration in range(2):
                output = work / f"tiled-{fixture}-{iteration}"
                run([cli, "convert", root / "fixtures" / fixture, "--output", output,
                     "--width", "80", "--base", "0.8", "--relief", "2.4", "--resolution", "64",
                     "--mode", mode, "--max-tile-width", "30", "--max-tile-height", "25", "--3mf"], work, env)
                check = json.loads(run([cli, "validate-assembly", output], work, env))
                assert check["passed"] and check["tile_count"] > 1
                assert json.loads(run([cli, "validate-3mf", output], work, env))["passed"]
                repeated.append({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()})
                assert (output / "assembly.svg").is_file()
                for tile in check["tiles"]:
                    assert json.loads(run([cli, "validate", output / tile["file"]], work, env))["passed"]
            assert repeated[0] == repeated[1]
            tiled_results.append({"fixture": fixture, "mode": mode, "tile_count": check["tile_count"],
                                  "triangles": check["triangle_count"], "validation": check["checks"],
                                  "manifest_sha256": repeated[0]["manifest.json"]})
        print(json.dumps({"isolated_install": True, "offline_install": True, "socket_guard": True,
                          "repeated_stl_bytes_identical": True, "conversions": results,
                          "repeated_tiled_bundle_bytes_identical": True, "tiled_conversions": tiled_results}, indent=2))


if __name__ == "__main__":
    main()
