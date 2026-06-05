"""Content-based image hashing for restoration cache deduplication.

The hash key is computed from the **raw pixel data** (not file bytes) combined
with the AI parameters that affect the output.  This means the same image
encoded as PNG or JPEG produces the same hash, while the same image with
different restoration parameters produces a different hash.
"""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image


@dataclass(frozen=True)
class RestorationParams:
    """AI restoration parameters that affect output quality.

    ``batch_size`` is intentionally excluded because it only affects
    processing speed, not the restoration result.
    """

    patch_size: int
    threshold: float
    binarize_output: bool
    overlap: bool


@dataclass(frozen=True)
class SoftRestorationParams:
    """AI restoration parameters that affect non-binarized output.

    ``threshold`` is intentionally excluded because soft output is produced
    before binarization. ``batch_size`` only affects processing speed.
    """

    patch_size: int
    overlap: bool


def compute_content_hash(image: Image.Image, params: RestorationParams) -> str:
    """Return the SHA-256 hex digest of raw pixel data + AI parameters.

    The image is normalised to RGB before hashing so that greyscale or
    RGBA inputs of the same visual content produce the same hash.
    """

    pixel_data = image.convert("RGB").tobytes()
    params_str = (
        f"{params.patch_size}_{params.threshold}"
        f"_{params.binarize_output}_{params.overlap}"
    )

    hasher = hashlib.sha256()
    hasher.update(pixel_data)
    hasher.update(params_str.encode())
    return hasher.hexdigest()


def compute_soft_content_hash(
    image: Image.Image, params: SoftRestorationParams,
) -> str:
    """Return the SHA-256 digest for soft restored output cache keys."""

    pixel_data = image.convert("RGB").tobytes()
    params_str = f"soft_{params.patch_size}_{params.overlap}"

    hasher = hashlib.sha256()
    hasher.update(pixel_data)
    hasher.update(params_str.encode())
    return hasher.hexdigest()


def compute_content_hash_from_path(
    path: Path, params: RestorationParams,
) -> str:
    """Open an image file and compute its content hash."""

    with Image.open(path) as img:
        return compute_content_hash(img, params)


def read_image_as_png_bytes(path: Path) -> bytes:
    """Open an image file and re-encode it as PNG bytes."""

    with Image.open(path) as img:
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
