"""Carga de imágenes respetando la orientación EXIF."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

try:  # soporte opcional de HEIC/HEIF (fotos de móvil)
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - dependencia opcional
    pass

Image.MAX_IMAGE_PIXELS = None  # son fotos del propio usuario


def tamano(ruta: Path) -> tuple[int, int]:
    """(ancho, alto) de la imagen ya orientada según EXIF."""
    with Image.open(ruta) as im:
        ancho, alto = im.size
        if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):
            ancho, alto = alto, ancho
    return ancho, alto


def cargar(ruta: Path, escala_min: float = 1.0) -> np.ndarray:
    """Carga la imagen como RGB uint8, ya orientada.

    ``escala_min`` (0-1] indica la escala mínima que se necesita respecto al original;
    en JPEG permite decodificar directamente a tamaño reducido (mucho más rápido).
    La imagen devuelta puede ser mayor que lo pedido, nunca menor.
    """
    with Image.open(ruta) as im:
        if escala_min < 1.0 and im.format == "JPEG":
            w, h = im.size
            im.draft("RGB", (math.ceil(w * escala_min), math.ceil(h * escala_min)))
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        return np.ascontiguousarray(np.asarray(im))
