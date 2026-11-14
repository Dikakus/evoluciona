"""Análisis de la secuencia (segmentación + alineación) y generación de fotogramas."""

from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from . import alineacion as al
from . import imagen
from .segmentacion import MODELOS, Segmentador, dir_cache

LADO_ANALISIS = 1024  # la segmentación no necesita más resolución


class Cancelado(Exception):
    pass


@dataclass(frozen=True)
class Opciones:
    metodo: str = "contorno"  # contorno | altura | area | ninguno
    referencia: str = "anterior"  # anterior | primera
    modelo: str = "general"  # general | persona | rapido
    recortar: bool = True  # recortar a la zona común a todas las fotos

    def validar(self) -> None:
        if self.metodo not in al.METODOS:
            raise ValueError(f"Método desconocido: {self.metodo}")
        if self.referencia not in ("anterior", "primera"):
            raise ValueError(f"Referencia desconocida: {self.referencia}")
        if self.modelo not in MODELOS:
            raise ValueError(f"Modelo desconocido: {self.modelo}")


@dataclass
class Proyecto:
    rutas: list[Path]
    tamanos: list[tuple[int, int]]
    transformaciones: list[al.Similitud]
    lienzo: tuple[int, int]
    recorte: tuple[int, int, int, int]  # x0, y0, x1, y1 en el lienzo
    opciones: Opciones
    avisos: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rutas)

    @property
    def tam_recorte(self) -> tuple[int, int]:
        x0, y0, x1, y1 = self.recorte
        return x1 - x0, y1 - y0

    def tam_salida(self, lado_max: int | None = None, alto: int | None = None, par: bool = False) -> tuple[int, int]:
        """Tamaño de salida conservando la proporción del recorte."""
        w, h = self.tam_recorte
        if alto:
            f = alto / h
        elif lado_max:
            f = min(1.0, lado_max / max(w, h))
        else:
            f = 1.0
        if par:  # los códecs de vídeo necesitan dimensiones pares
            return max(2, 2 * round(w * f / 2)), max(2, 2 * round(h * f / 2))
        return max(1, round(w * f)), max(1, round(h * f))

    def fotograma(self, i: int, tam: tuple[int, int]) -> np.ndarray:
        """Foto ``i`` alineada y recortada, como RGB uint8 de tamaño ``tam`` (ancho, alto)."""
        t = self.transformaciones[i]
        w, h = self.tamanos[i]
        cw, ch = self.tam_recorte
        sx, sy = tam[0] / cw, tam[1] / ch
        escala = t.s * max(sx, sy)  # píxeles de salida por píxel de la foto original
        img = imagen.cargar(self.rutas[i], escala_min=min(1.0, escala))
        # si aún sobra mucha resolución, se reduce con promediado para evitar aliasing
        f = escala * w / img.shape[1]
        if f < 0.75:
            img = cv2.resize(img, (max(1, round(img.shape[1] * f)), max(1, round(img.shape[0] * f))),
                             interpolation=cv2.INTER_AREA)
        ex, ey = img.shape[1] / w, img.shape[0] / h  # píxeles cargados por píxel original
        x0, y0 = self.recorte[0], self.recorte[1]
        # se alinean centros de píxel (el +0.5/-0.5) para no desplazar al cambiar de escala
        M = np.array([
            [sx * t.s / ex, 0.0, sx * (t.tx - x0) + 0.5 * (sx * t.s / ex) - 0.5],
            [0.0, sy * t.s / ey, sy * (t.ty - y0) + 0.5 * (sy * t.s / ey) - 0.5],
        ])
        return cv2.warpAffine(img, M, tam, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)


def _clave_mascara(ruta: Path, modelo: str) -> Path:
    st = ruta.stat()
    texto = f"{ruta.resolve()}|{st.st_mtime_ns}|{st.st_size}|{modelo}|v1"
    return dir_cache() / "mascaras" / (hashlib.sha1(texto.encode()).hexdigest() + ".png")


def _recorte_comun(tamanos, transformaciones, lienzo, avisos) -> tuple[int, int, int, int]:
    W, H = lienzo
    x0, y0, x1, y1 = 0.0, 0.0, float(W), float(H)
    for (w, h), t in zip(tamanos, transformaciones):
        x0, y0 = max(x0, t.tx), max(y0, t.ty)
        x1, y1 = min(x1, t.tx + t.s * w), min(y1, t.ty + t.s * h)
    rx0, ry0, rx1, ry1 = math.ceil(x0 - 1e-6), math.ceil(y0 - 1e-6), math.floor(x1 + 1e-6), math.floor(y1 + 1e-6)
    if rx1 - rx0 < 16 or ry1 - ry0 < 16 or (rx1 - rx0) * (ry1 - ry0) < 0.35 * W * H:
        avisos.append("La zona común a todas las fotos es demasiado pequeña: no se recorta "
                      "(se verán bordes negros).")
        return 0, 0, W, H
    return rx0, ry0, rx1, ry1


def analizar(
    rutas: list[Path],
    opciones: Opciones = Opciones(),
    progreso: Callable[[int, int, str], None] | None = None,
    cancelado: Callable[[], bool] | None = None,
) -> Proyecto:
    """Segmenta cada foto y calcula la transformación que alinea las figuras."""
    opciones.validar()
    if not rutas:
        raise ValueError("No hay fotografías")
    n = len(rutas)
    avisos: list[str] = []

    def informar(i: int, texto: str) -> None:
        if cancelado and cancelado():
            raise Cancelado()
        if progreso:
            progreso(i, n, texto)

    tamanos: list[tuple[int, int]] = []
    for i, r in enumerate(rutas):
        informar(i, f"Leyendo {r.name}")
        try:
            tamanos.append(imagen.tamano(r))
        except Exception as e:
            raise ValueError(f"No se puede leer la imagen {r}: {e}") from e

    lienzo = tamanos[0]
    transformaciones: list[al.Similitud] = []
    segmentador: Segmentador | None = None
    ref: al.Referencia | None = None
    sin_figura: list[str] = []

    for i, r in enumerate(rutas):
        w, h = tamanos[i]
        if opciones.metodo == "ninguno":
            transformaciones.append(al.cubrir(w, h, lienzo))
            continue
        informar(i, f"Analizando {r.name}")

        fichero = _clave_mascara(r, opciones.modelo)
        mascara = cv2.imread(str(fichero), cv2.IMREAD_GRAYSCALE) if fichero.exists() else None
        if mascara is None:
            if segmentador is None:
                segmentador = Segmentador(opciones.modelo, aviso=lambda t: informar(i, t))
            mascara = segmentador.mascara(imagen.cargar(r, escala_min=LADO_ANALISIS / max(w, h)))
            try:
                fichero.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(fichero), mascara)
            except OSError:
                pass  # la caché es solo una optimización

        figura, limpia = al.describir(mascara, w, h)
        if figura is None:
            sin_figura.append(r.name)
            t = al.cubrir(w, h, lienzo)
        elif ref is None:
            t = al.cubrir(w, h, lienzo)  # la primera foto con figura fija el encuadre
        else:
            t = al.alinear(figura, limpia, (w, h), ref, lienzo, opciones.metodo)
        transformaciones.append(t)

        if figura is not None and (ref is None or opciones.referencia == "anterior"):
            m, v = al.a_lienzo(limpia, t, (w, h), lienzo)
            ref = al.Referencia(figura.transformada(t), m, v)

    if sin_figura:
        nombres = ", ".join(sin_figura[:5]) + ("…" if len(sin_figura) > 5 else "")
        avisos.append(f"No se detectó ninguna figura en {len(sin_figura)} foto(s) ({nombres}): "
                      "se muestran sin alinear.")

    recorte = (0, 0, lienzo[0], lienzo[1])
    if opciones.recortar:
        recorte = _recorte_comun(tamanos, transformaciones, lienzo, avisos)
    informar(n, "Listo")
    return Proyecto(list(rutas), tamanos, transformaciones, lienzo, recorte, opciones, avisos)


class CacheFotogramas:
    """Caché LRU en memoria de fotogramas ya alineados, con precarga en segundo plano."""

    def __init__(self, proyecto: Proyecto, tam: tuple[int, int], max_bytes: int = 1 << 30, hilos: int = 2):
        self.proyecto = proyecto
        self.tam = tam
        self.max_bytes = max_bytes
        self._datos: OrderedDict[int, np.ndarray] = OrderedDict()
        self._pendientes: dict[int, Future] = {}
        self._bytes = 0
        self._cerrojo = threading.Lock()
        self._ejecutor = ThreadPoolExecutor(max_workers=hilos, thread_name_prefix="fotograma")

    def _calcular(self, i: int) -> np.ndarray:
        try:
            img = self.proyecto.fotograma(i, self.tam)
        except BaseException:
            with self._cerrojo:
                self._pendientes.pop(i, None)
            raise
        with self._cerrojo:
            self._pendientes.pop(i, None)
            if i not in self._datos:
                self._datos[i] = img
                self._bytes += img.nbytes
                while self._bytes > self.max_bytes and len(self._datos) > 2:
                    _, viejo = self._datos.popitem(last=False)
                    self._bytes -= viejo.nbytes
        return img

    def precargar(self, i: int) -> None:
        if not 0 <= i < len(self.proyecto):
            return
        with self._cerrojo:
            if i in self._datos or i in self._pendientes:
                return
            self._pendientes[i] = self._ejecutor.submit(self._calcular, i)

    def obtener(self, i: int) -> np.ndarray:
        with self._cerrojo:
            if i in self._datos:
                self._datos.move_to_end(i)
                return self._datos[i]
            futuro = self._pendientes.get(i)
            if futuro is None:
                futuro = self._pendientes[i] = self._ejecutor.submit(self._calcular, i)
        return futuro.result()

    def cerrar(self) -> None:
        self._ejecutor.shutdown(wait=False, cancel_futures=True)
