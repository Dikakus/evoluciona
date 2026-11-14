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


USAR_GPU = True  # la CLI lo pone a False con --cpu; también EVOLUCIONA_DISPOSITIVO=cpu
CUDA = "CUDAExecutionProvider"


def _importar_ort():
    try:
        import onnxruntime as ort
    except ImportError as e:
        raise RuntimeError("Falta onnxruntime: instala evoluciona con el extra [cpu] o [gpu] "
                           "(p. ej. pip install 'evoluciona[cpu]').") from e
    return ort


def gpu_preferida() -> bool:
    return USAR_GPU and os.environ.get("EVOLUCIONA_DISPOSITIVO", "auto").strip().lower() != "cpu"


def cuda_disponible(ort) -> bool:
    """Si onnxruntime trae el proveedor CUDA (paquete onnxruntime-gpu). No garantiza que funcione."""
    try:
        return CUDA in ort.get_available_providers()
    except Exception:
        return False


def _precargar_cuda(ort) -> None:
    """Carga las librerías CUDA/cuDNN instaladas como paquetes de Python (nvidia-*), si las hay."""
    if hasattr(ort, "preload_dlls"):
        try:
            ort.preload_dlls()
        except Exception:
            pass  # si faltan, la creación de la sesión lo detectará


class Segmentador:
    """Calcula la máscara (0-255) de la figura destacada de una imagen.

    Usa la GPU (CUDA) cuando onnxruntime-gpu está instalado y funciona de verdad: se comprueba en cada
    ejecución con una inferencia de prueba. Si algo falla se usa la CPU y ``aviso_gpu`` explica por qué.
    """

    def __init__(self, modelo: str = "general", aviso: Callable[[str], None] | None = None):
        ort = _importar_ort()
        ruta = str(ruta_modelo(modelo, aviso))
        self.dispositivo = "CPU"
        self.aviso_gpu: str | None = None
        self.sesion = None
        if gpu_preferida() and cuda_disponible(ort):
            try:
                _precargar_cuda(ort)
                sesion = ort.InferenceSession(ruta, sess_options=self._opciones(ort, gpu=True),
                                              providers=[CUDA, "CPUExecutionProvider"])
                if sesion.get_providers()[0] != CUDA:
                    raise RuntimeError("onnxruntime no pudo iniciar CUDA (¿faltan las librerías CUDA/cuDNN "
                                       "o el controlador de NVIDIA es demasiado antiguo?)")
                entrada = sesion.get_inputs()[0].name
                sesion.run(None, {entrada: np.zeros((1, 3, 320, 320), np.float32)})  # prueba real
                self.sesion, self.dispositivo = sesion, "GPU (CUDA)"
            except Exception as e:
                self.aviso_gpu = f"No se pudo usar la GPU; se usa la CPU. Motivo: {str(e).strip()[:300]}"
        if self.sesion is None:
            self.sesion = ort.InferenceSession(ruta, sess_options=self._opciones(ort, gpu=False),
                                               providers=["CPUExecutionProvider"])
        self.entrada = self.sesion.get_inputs()[0].name
        self._ruta = ruta

    def _pasar_a_cpu(self, motivo: Exception) -> None:
        ort = _importar_ort()
        self.sesion = ort.InferenceSession(self._ruta, sess_options=self._opciones(ort, gpu=False),
                                           providers=["CPUExecutionProvider"])
        self.entrada = self.sesion.get_inputs()[0].name
        self.dispositivo = "CPU"
        self.aviso_gpu = f"La GPU falló durante el análisis; se continúa con la CPU. Motivo: {str(motivo).strip()[:300]}"

    @staticmethod
    def _opciones(ort, gpu: bool):
        opciones = ort.SessionOptions()
        if not gpu:
            opciones.intra_op_num_threads = os.cpu_count() or 4
        opciones.log_severity_level = 4 if gpu else 3  # el intento con GPU no debe llenar la terminal
        return opciones

    def mascara(self, rgb: np.ndarray) -> np.ndarray:
        """Máscara uint8 con la misma proporción que la imagen y lado mayor LADO_MASCARA."""
        alto, ancho = rgb.shape[:2]
        x = cv2.resize(rgb, (320, 320), interpolation=cv2.INTER_AREA).astype(np.float32)
        x /= max(float(x.max()), 1e-6)
        x = (x - (0.485, 0.456, 0.406)) / (0.229, 0.224, 0.225)
        x = np.ascontiguousarray(x.transpose(2, 0, 1)[None].astype(np.float32))
        try:
            y = self.sesion.run(None, {self.entrada: x})[0][0, 0]
        except Exception as e:
            if self.dispositivo == "CPU":
                raise
            self._pasar_a_cpu(e)
            y = self.sesion.run(None, {self.entrada: x})[0][0, 0]
        y = (y - y.min()) / max(float(y.max() - y.min()), 1e-6)
        f = LADO_MASCARA / max(ancho, alto)
        tam = (max(1, round(ancho * f)), max(1, round(alto * f)))
        y = cv2.resize(y, tam, interpolation=cv2.INTER_LINEAR)
        return np.clip(y * 255.0 + 0.5, 0, 255).astype(np.uint8)


def diagnostico() -> list[str]:
    """Describe el soporte de GPU sin necesidad de abrir ninguna foto."""
    import shutil
    import subprocess
    from importlib import metadata

    lineas = []
    try:
        ort = _importar_ort()
    except RuntimeError as e:
        return [str(e)]
    paquetes = []
    for nombre in ("onnxruntime", "onnxruntime-gpu"):
        try:
            paquetes.append(f"{nombre} {metadata.version(nombre)}")
        except metadata.PackageNotFoundError:
            pass
    lineas.append(f"onnxruntime: {', '.join(paquetes) or ort.__version__}")
    lineas.append(f"Proveedores: {', '.join(ort.get_available_providers())}")
    nvsmi = shutil.which("nvidia-smi") or next(
        (r for r in ("/usr/lib/wsl/lib/nvidia-smi",) if os.access(r, os.X_OK)), None)  # WSL
    if nvsmi:
        try:
            r = subprocess.run([nvsmi, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=10)
            lineas.append(f"GPU NVIDIA: {r.stdout.strip() or r.stderr.strip() or 'no responde'}")
        except Exception as e:
            lineas.append(f"GPU NVIDIA: nvidia-smi falla ({e})")
    else:
        lineas.append("GPU NVIDIA: no detectada (no hay nvidia-smi)")
    if not gpu_preferida():
        lineas.append("Dispositivo: CPU (forzado con --cpu o EVOLUCIONA_DISPOSITIVO=cpu)")
    elif not cuda_disponible(ort):
        lineas.append("Dispositivo: CPU (este onnxruntime no incluye CUDA)")
    else:
        # se valida con el modelo más pequeño que ya esté descargado (no se descarga nada)
        descargados = [m for m in ("rapido", "persona", "general")
                       if (dir_cache() / "modelos" / f"{MODELOS[m][0]}.onnx").exists()]
        if not descargados:
            lineas.append("Dispositivo: GPU (CUDA) disponible; se verificará al analizar la primera foto")
        else:
            s = Segmentador(descargados[0])
            lineas.append(f"Dispositivo: {s.dispositivo} (verificado)" + (f". {s.aviso_gpu}" if s.aviso_gpu else ""))
    return lineas
