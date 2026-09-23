"""Bounded image decoding, explicit brightness mapping, and conversion bundles."""

from dataclasses import asdict, dataclass
import hashlib
import io
import json
import math
import shutil
import tempfile
import warnings
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError, __version__ as pillow_version

from . import __version__
from .geometry import expected_volume, triangulate, write_stl
from .validation import validate_stl
from .assembly import validate_assembly
from .tiling import export_tiles

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_INPUT_PIXELS = 20_000_000
MAX_RESOLUTION = 256


@dataclass(frozen=True)
class Parameters:
    width: float = 100.0
    base: float = 1.0
    relief: float = 3.0
    resolution: int = 128
    mode: str = "relief"
    invert: bool = False
    max_tile_width: float | None = None
    max_tile_height: float | None = None

    def validate(self):
        for name, lower, upper in (("width", 0.1, 2000), ("base", 0.01, 100), ("relief", 0, 100)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{name} must be finite and between {lower} and {upper} mm")
        if isinstance(self.resolution, bool) or not isinstance(self.resolution, int) or not 2 <= self.resolution <= MAX_RESOLUTION:
            raise ValueError(f"resolution must be an integer between 2 and {MAX_RESOLUTION}")
        if self.mode not in ("relief", "lithophane"):
            raise ValueError("mode must be relief or lithophane")
        if (self.max_tile_width is None) != (self.max_tile_height is None):
            raise ValueError("provide both --max-tile-width and --max-tile-height")
        for name in ("max_tile_width", "max_tile_height"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or
                                      not math.isfinite(value) or not 0 < value <= 2000):
                raise ValueError(f"{name} must be finite, greater than zero and at most 2000 mm")
        if not isinstance(self.invert, bool):
            raise ValueError("invert must be boolean")


def read_image(path: Path):
    if not path.is_file():
        raise ValueError("input must be a regular PNG or JPEG file")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"input exceeds {MAX_INPUT_BYTES} byte limit")
    with path.open("rb") as stream:
        data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"input exceeds {MAX_INPUT_BYTES} byte limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in ("PNG", "JPEG"):
                    raise ValueError("only PNG and JPEG images are supported")
                if source.width * source.height > MAX_INPUT_PIXELS:
                    raise ValueError(f"image exceeds {MAX_INPUT_PIXELS} pixel limit")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("animated images are not supported")
                source_format, original_size = source.format, list(source.size)
                # Pillow's I/I;16 -> RGB clamps; refuse ambiguous high-bit-depth data.
                if source.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "CMYK"):
                    raise ValueError(f"unsupported image mode {source.mode}; convert to 8-bit RGB first")
                source.verify()
            # verify() checks PNG chunk integrity but consumes the decoder.
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                rgba = oriented.convert("RGBA")
                white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                image = Image.alpha_composite(white, rgba).convert("L")
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError(f"cannot decode image: {exc}") from exc
    return image, {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                   "format": source_format, "original_size_px": original_size,
                   "oriented_size_px": list(image.size)}


def convert(input_path, output_directory, parameters: Parameters = Parameters()) -> dict:
    parameters.validate()
    input_path, output = Path(input_path), Path(output_directory)
    if output.exists() or output.is_symlink():
        raise ValueError("output directory already exists; choose a new directory")
    image, input_info = read_image(input_path)
    width = float(parameters.width)
    depth = width * image.height / image.width
    if not 0.1 <= depth <= 2000:
        raise ValueError("aspect-preserving height must be between 0.1 and 2000 mm; change --width")
    longest = max(image.size)
    nx, ny = (max(2, int(math.floor(parameters.resolution * side / longest + 0.5))) for side in image.size)
    sampled = image.resize((nx, ny), Image.Resampling.BILINEAR)
    brightness = np.asarray(sampled, dtype=np.float64) / 255.0
    reverse = (parameters.mode == "lithophane") != parameters.invert
    mapped = 1 - brightness if reverse else brightness
    heights = parameters.base + parameters.relief * mapped
    bounds = [[0, 0, 0], [width, depth, float(heights.max())]]
    volume = expected_volume(heights, width, depth)
    tiled = parameters.max_tile_width is not None
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".relief-forge-", dir=output.parent) as staging:
        stage = Path(staging)
        # White means maximum configured relief, including in lithophane mode.
        preview = Image.fromarray(np.rint(mapped * 255).astype(np.uint8))
        preview.save(stage / "height.png")
        if tiled:
            export_tiles(stage, heights, width, depth, parameters.max_tile_width, parameters.max_tile_height)
            validation = validate_assembly(stage)
            artifacts = {"manifest": {"file": "manifest.json"}, "assembly_map": {"file": "assembly.svg"},
                         "surface": {"file": "surface.npy"}}
        else:
            stl = stage / "model.stl"
            write_stl(stl, triangulate(heights, width, depth))
            validation = validate_stl(stl, expected_bounds=bounds, expected_volume=volume)
            artifacts = {"stl": {"file": "model.stl", "sha256": hashlib.sha256(stl.read_bytes()).hexdigest()}}
        if not validation["passed"]:
            failed = [key for key, value in validation["checks"].items() if not value]
            raise ValueError(f"exported STL failed validation: {', '.join(failed)}")
        artifacts["preview"] = {"file": "height.png", "meaning": "0=base; 255=base+relief; image row orientation"}
        parameter_info = asdict(parameters)
        if not tiled:
            parameter_info.pop("max_tile_width")
            parameter_info.pop("max_tile_height")
        report = {
            "schema_version": 2 if tiled else 1, "generator": {"name": "image-relief-forge", "version": __version__,
                                                "numpy": np.__version__, "pillow": pillow_version},
            "units": "mm", "input": input_info, "parameters": parameter_info,
            "sampling": {"grid_vertices_xy": [nx, ny], "filter": "Pillow BILINEAR",
                         "spacing_mm_xy": [width / (nx - 1), depth / (ny - 1)]},
            "mapping": {"brightness": "Pillow 8-bit L (encoded RGB luma, not linear light)",
                        "alpha": "composite over white before grayscale", "exif": "transpose before sizing",
                        "formula": "base + relief * (1 - brightness)" if reverse else "base + relief * brightness",
                        "orientation": "image left to +X; image top at +Y; thickness along +Z; bottom Z=0",
                        "icc": "embedded color profiles are not applied"},
            "expected_bounds_mm": bounds, "expected_volume_mm3": volume,
            "surface_height_range_mm": [float(heights.min()), float(heights.max())],
            "validation": validation,
            "artifacts": artifacts,
            "limitations": ["Brightness relief does not recover object geometry.",
                            "Geometry checks do not establish physical print quality.",
                            "STL stores unitless float32 coordinates; import as millimeters."]}
        (stage / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        # Exclusive mkdir refuses existing files/directories; staged checks finish first.
        output.mkdir()
        try:
            for artifact in sorted(stage.iterdir()):
                shutil.move(str(artifact), str(output / artifact.name))
        except BaseException:
            shutil.rmtree(output)
            raise
    return report
