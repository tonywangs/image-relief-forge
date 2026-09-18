"""Create small, clearly synthetic image fixtures. No downloads or randomness."""

from pathlib import Path

import numpy as np
from PIL import Image


def main():
    target = Path(__file__).resolve().parents[1] / "fixtures"
    target.mkdir(exist_ok=True)
    gradient = np.tile(np.arange(0, 256, 4, dtype=np.uint8), (32, 1))
    Image.fromarray(gradient).save(target / "gradient.png")
    Image.fromarray(gradient).save(target / "gradient.jpg", quality=95, subsampling=0)
    for name, value in (("black", 0), ("gray", 128), ("white", 255)):
        Image.new("L", (16, 12), value).save(target / f"{name}.png")
    # Distinct corner levels expose mirrors, rotations, and inversions.
    marker = np.full((12, 20), 96, dtype=np.uint8)
    marker[:4, :4], marker[:4, -4:] = 0, 64
    marker[-4:, :4], marker[-4:, -4:] = 192, 255
    Image.fromarray(marker).save(target / "orientation.png")
    alpha = Image.new("RGBA", (16, 8), (0, 0, 0, 0))
    for y in range(8):
        for x in range(8):
            alpha.putpixel((x, y), (0, 0, 0, 255))
    alpha.save(target / "transparent.png")
    Image.new("L", (1000, 1), 128).save(target / "wide.png")
    Image.new("L", (1, 1000), 128).save(target / "tall.png")


if __name__ == "__main__":
    main()
