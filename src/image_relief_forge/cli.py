import argparse
import json
import sys

from . import __version__
from .conversion import Parameters, convert
from .validation import validate_stl


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert PNG/JPEG brightness into a closed STL tile. Units: mm.")
    parser.add_argument("--version", action="version", version=f"image-relief-forge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("convert", help="create a new directory containing STL, preview, and report")
    build.add_argument("input")
    build.add_argument("--output", required=True, help="new output directory (never overwrite)")
    build.add_argument("--width", type=float, default=100, help="physical width in mm (default: 100)")
    build.add_argument("--base", type=float, default=1, help="minimum thickness in mm (default: 1)")
    build.add_argument("--relief", type=float, default=3, help="brightness-driven added thickness in mm (default: 3)")
    build.add_argument("--resolution", type=int, default=128, help="vertices along longest image axis, 2..256 (default: 128)")
    build.add_argument("--mode", choices=["relief", "lithophane"], default="relief")
    build.add_argument("--invert", action="store_true", help="reverse the chosen mode's brightness mapping")
    check = sub.add_parser("validate", help="check an existing binary STL without repairs; emit JSON")
    check.add_argument("stl")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            report = validate_stl(args.stl)
            print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
            return 0 if report["passed"] else 1
        params = Parameters(width=args.width, base=args.base, relief=args.relief,
                            resolution=args.resolution, mode=args.mode, invert=args.invert)
        report = convert(args.input, args.output, params)
        print(json.dumps({"output": args.output, "passed": report["validation"]["passed"],
                          "triangles": report["validation"]["triangle_count"]}, sort_keys=True))
        return 0
    except (ValueError, OSError) as exc:
        print(f"relief-forge: {exc}", file=sys.stderr)
        return 2
