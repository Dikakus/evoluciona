"""Segmentación de la figura en primer plano con U2-Net (ONNX)."""

from __future__ import annotations

import hashlib
import os
import urllib.request
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/{}.onnx"

# nombre público -> (fichero, md5)
MODELOS = {
    "general": ("u2net", "60024c5c889badc19c04ad937298a77b"),
    "persona": ("u2net_human_seg", "c09ddc2e0104f800e3e1bb4652583d1f"),
    "rapido": ("u2netp", "8e83ca70e441ab06c318d82300c84806"),
}

LADO_MASCARA = 320  # lado mayor de la máscara que se guarda


def dir_cache() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "evoluciona"


def _md5(ruta: Path) -> str:
    h = hashlib.md5()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def ruta_modelo(modelo: str, aviso: Callable[[str], None] | None = None) -> Path:
    """Devuelve la ruta del modelo, descargándolo la primera vez."""
    if modelo not in MODELOS:
        raise ValueError(f"Modelo desconocido: {modelo}")
    nombre, md5 = MODELOS[modelo]
    destino = dir_cache() / "modelos" / f"{nombre}.onnx"
    if destino.exists():
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    if aviso:
        aviso(f"Descargando el modelo de segmentación «{modelo}» (solo la primera vez)…")
    parcial = destino.with_suffix(".parcial")
    try:
        with urllib.request.urlopen(_URL.format(nombre), timeout=60) as resp, open(parcial, "wb") as f:
            while bloque := resp.read(1 << 20):
                f.write(bloque)
        if _md5(parcial) != md5:
            raise RuntimeError("la suma de comprobación no coincide")
        parcial.replace(destino)
    except Exception as e:
        parcial.unlink(missing_ok=True)
        raise RuntimeError(f"No se pudo descargar el modelo «{modelo}»: {e}") from e
    return destino


class Segmentador:
    """Calcula la máscara (0-255) de la figura destacada de una imagen."""

    def __init__(self, modelo: str = "general", aviso: Callable[[str], None] | None = None):
        import onnxruntime as ort

        opciones = ort.SessionOptions()
        opciones.intra_op_num_threads = os.cpu_count() or 4
        opciones.log_severity_level = 3
        self.sesion = ort.InferenceSession(
            str(ruta_modelo(modelo, aviso)), sess_options=opciones, providers=["CPUExecutionProvider"]
        )
        self.entrada = self.sesion.get_inputs()[0].name

    def mascara(self, rgb: np.ndarray) -> np.ndarray:
        """Máscara uint8 con la misma proporción que la imagen y lado mayor LADO_MASCARA."""
        alto, ancho = rgb.shape[:2]
        x = cv2.resize(rgb, (320, 320), interpolation=cv2.INTER_AREA).astype(np.float32)
        x /= max(float(x.max()), 1e-6)
        x = (x - (0.485, 0.456, 0.406)) / (0.229, 0.224, 0.225)
        x = np.ascontiguousarray(x.transpose(2, 0, 1)[None].astype(np.float32))
        y = self.sesion.run(None, {self.entrada: x})[0][0, 0]
        y = (y - y.min()) / max(float(y.max() - y.min()), 1e-6)
        f = LADO_MASCARA / max(ancho, alto)
        tam = (max(1, round(ancho * f)), max(1, round(alto * f)))
        y = cv2.resize(y, tam, interpolation=cv2.INTER_LINEAR)
        return np.clip(y * 255.0 + 0.5, 0, 255).astype(np.uint8)
