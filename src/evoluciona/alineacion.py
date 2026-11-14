"""Alineación de la figura en primer plano mediante escala y traslación.

Todas las transformaciones son semejanzas sin rotación: ``x' = s·x + tx``,
``y' = s·y + ty``, y llevan coordenadas de la foto original al lienzo común.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from scipy.optimize import minimize

METODOS = ("contorno", "altura", "area", "ninguno")
LADO_LIENZO_PEQUENO = 256  # resolución de trabajo para comparar siluetas


@dataclass(frozen=True)
class Similitud:
    s: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def matriz(self) -> np.ndarray:
        return np.array([[self.s, 0.0, self.tx], [0.0, self.s, self.ty]], dtype=np.float64)


@dataclass(frozen=True)
class Figura:
    """Descripción geométrica de la silueta (en píxeles de su sistema de coordenadas)."""

    cx: float  # centroide
    cy: float
    x0: float  # caja robusta
    y0: float
    x1: float
    y1: float
    area: float

    @property
    def alto(self) -> float:
        return self.y1 - self.y0

    @property
    def ancho(self) -> float:
        return self.x1 - self.x0

    def transformada(self, t: Similitud) -> "Figura":
        return Figura(
            t.s * self.cx + t.tx, t.s * self.cy + t.ty,
            t.s * self.x0 + t.tx, t.s * self.y0 + t.ty,
            t.s * self.x1 + t.tx, t.s * self.y1 + t.ty,
            self.area * t.s * t.s,
        )


@dataclass
class Referencia:
    """Figura ya colocada en el lienzo, contra la que se alinea la siguiente."""

    figura: Figura  # en píxeles del lienzo
    mascara: np.ndarray  # float32 0-1, lienzo a baja resolución
    valido: np.ndarray  # float32 0-1: zona del lienzo cubierta por la foto


def _percentiles(perfil: np.ndarray, bajo: float, alto: float) -> tuple[float, float]:
    acum = np.cumsum(perfil)
    total = acum[-1]
    a = float(np.searchsorted(acum, total * bajo))
    b = float(np.searchsorted(acum, total * alto)) + 1.0
    return a, b


def describir(mascara: np.ndarray, ancho: int, alto: int) -> tuple[Figura | None, np.ndarray]:
    """Limpia la máscara (uint8) y describe la figura en píxeles de la foto original.

    Devuelve ``(figura, mascara_limpia_float32)``; ``figura`` es ``None`` si no se
    detecta nada aprovechable.
    """
    hm, wm = mascara.shape
    binaria = (mascara > 127).astype(np.uint8)
    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(binaria, connectivity=8)
    limpia = np.zeros((hm, wm), np.float32)
    if n <= 1:
        return None, limpia
    areas = stats[1:, cv2.CC_STAT_AREA]
    mayor = int(areas.max())
    if mayor < 0.004 * hm * wm:
        return None, limpia
    # la figura es el componente mayor más los que no sean despreciables frente a él
    conservar = np.zeros(n, bool)
    conservar[1:] = areas >= 0.1 * mayor
    sel = conservar[etiquetas]
    limpia[sel] = mascara[sel].astype(np.float32) / 255.0

    fx, fy = ancho / wm, alto / hm
    cols, filas = sel.sum(0).astype(np.float64), sel.sum(1).astype(np.float64)
    x0, x1 = _percentiles(cols, 0.01, 0.99)
    y0, y1 = _percentiles(filas, 0.01, 0.99)
    total = cols.sum()
    cx = float((cols * (np.arange(wm) + 0.5)).sum() / total)
    cy = float((filas * (np.arange(hm) + 0.5)).sum() / total)
    figura = Figura(cx * fx, cy * fy, x0 * fx, y0 * fy, x1 * fx, y1 * fy, float(total) * fx * fy)
    if figura.alto <= 0 or figura.ancho <= 0:
        return None, limpia
    return figura, limpia


def cubrir(ancho: int, alto: int, lienzo: tuple[int, int]) -> Similitud:
    """Escala la foto para que cubra todo el lienzo, centrada."""
    W, H = lienzo
    s = max(W / ancho, H / alto)
    return Similitud(s, (W - s * ancho) / 2.0, (H - s * alto) / 2.0)


def escala_lienzo(lienzo: tuple[int, int]) -> tuple[float, tuple[int, int]]:
    W, H = lienzo
    q = LADO_LIENZO_PEQUENO / max(W, H)
    return q, (max(1, round(W * q)), max(1, round(H * q)))


def _matriz_pequena(t: Similitud, tam_foto: tuple[int, int], forma_mascara: tuple[int, int], q: float) -> np.ndarray:
    """Matriz que lleva la máscara (baja resolución, de la foto) al lienzo pequeño."""
    hm, wm = forma_mascara
    ex, ey = tam_foto[0] / wm, tam_foto[1] / hm  # píxeles de foto por píxel de máscara
    return np.array([[q * t.s * ex, 0.0, q * t.tx], [0.0, q * t.s * ey, q * t.ty]], dtype=np.float64)


def a_lienzo(mascara: np.ndarray, t: Similitud, tam_foto: tuple[int, int], lienzo: tuple[int, int]):
    """Proyecta la máscara de una foto al lienzo pequeño. Devuelve (máscara, válido)."""
    q, tam = escala_lienzo(lienzo)
    M = _matriz_pequena(t, tam_foto, mascara.shape, q)
    m = cv2.warpAffine(mascara, M, tam, flags=cv2.INTER_LINEAR)
    v = cv2.warpAffine(np.ones_like(mascara), M, tam, flags=cv2.INTER_LINEAR)
    return m, v


def _inicial(fig: Figura, ref: Figura, metodo: str) -> Similitud:
    if metodo == "area":
        s = math.sqrt(ref.area / fig.area)
        return Similitud(s, ref.cx - s * fig.cx, ref.cy - s * fig.cy)
    # por altura: misma altura de silueta, centroide horizontal y centro vertical de la caja
    s = ref.alto / fig.alto
    return Similitud(s, ref.cx - s * fig.cx, (ref.y0 + ref.y1) / 2.0 - s * (fig.y0 + fig.y1) / 2.0)


def _refinar(
    fig: Figura, mascara: np.ndarray, tam_foto: tuple[int, int],
    ref: Referencia, lienzo: tuple[int, int], t0: Similitud,
) -> Similitud:
    """Ajusta escala y posición maximizando el solape (IoU) de las siluetas."""
    q, tam = escala_lienzo(lienzo)
    suave = cv2.GaussianBlur(mascara, (0, 0), 1.2)
    ref_m = cv2.GaussianBlur(ref.mascara, (0, 0), 1.2)
    unos = np.ones_like(mascara)
    masa_ref = float(ref_m.sum()) + 1e-6
    # tamaño característico de la figura en el lienzo, para normalizar los desplazamientos
    escala_fig = max(ref.figura.alto, ref.figura.ancho)
    c0x, c0y = t0.s * fig.cx + t0.tx, t0.s * fig.cy + t0.ty
    LIM = 0.45

    def transformacion(p) -> Similitud:
        s = t0.s * math.exp(p[0])
        cx, cy = c0x + p[1] * escala_fig, c0y + p[2] * escala_fig
        return Similitud(s, cx - s * fig.cx, cy - s * fig.cy)

    def coste(p) -> float:
        if max(abs(p[0]), abs(p[1]), abs(p[2])) > LIM:
            return 3.0
        M = _matriz_pequena(transformacion(p), tam_foto, mascara.shape, q)
        m = cv2.warpAffine(suave, M, tam, flags=cv2.INTER_LINEAR)
        v = cv2.warpAffine(unos, M, tam, flags=cv2.INTER_LINEAR) * ref.valido
        a, b = m * v, ref_m * v
        union = float(np.maximum(a, b).sum())
        if union < 1e-6:
            return 3.0
        iou = float(np.minimum(a, b).sum()) / union
        # penaliza dejar la figura de referencia fuera de la zona comparable
        oculto = 1.0 - float(b.sum()) / masa_ref
        return (1.0 - iou) + 0.5 * oculto

    mejor_p, mejor_c = np.zeros(3), coste(np.zeros(3))
    for ds in (0.0, -0.12, 0.12):  # varios arranques en escala para evitar mínimos locales
        p0 = np.array([ds, 0.0, 0.0])
        simplex = np.vstack([p0, p0 + (0.06, 0, 0), p0 + (0, 0.06, 0), p0 + (0, 0, 0.06)])
        r = minimize(coste, p0, method="Nelder-Mead",
                     options={"initial_simplex": simplex, "xatol": 5e-4, "fatol": 1e-5, "maxiter": 250})
        if r.fun < mejor_c:
            mejor_p, mejor_c = r.x, float(r.fun)
    return transformacion(mejor_p)


def alinear(
    fig: Figura, mascara: np.ndarray, tam_foto: tuple[int, int],
    ref: Referencia, lienzo: tuple[int, int], metodo: str = "contorno",
) -> Similitud:
    """Transformación que superpone la figura de la foto sobre la de referencia."""
    if metodo not in ("contorno", "altura", "area"):
        raise ValueError(f"Método desconocido: {metodo}")
    t0 = _inicial(fig, ref.figura, metodo)
    if metodo == "contorno":
        return _refinar(fig, mascara, tam_foto, ref, lienzo, t0)
    return t0
