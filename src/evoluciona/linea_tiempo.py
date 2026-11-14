"""Línea de tiempo común a la reproducción en pantalla y a la exportación de vídeo.

Cada foto se mantiene ``t_foto`` segundos y, si hay fundido, la transición a la
siguiente dura ``t_transicion`` segundos. Sin fundido el cambio es un corte.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ritmo:
    t_foto: float = 1.0
    t_transicion: float = 1.0
    fundido: bool = True

    @property
    def transicion_efectiva(self) -> float:
        return max(0.0, self.t_transicion) if self.fundido else 0.0

    @property
    def paso(self) -> float:
        return max(0.0, self.t_foto) + self.transicion_efectiva

    def duracion(self, n: int) -> float:
        if n <= 0:
            return 0.0
        return n * max(0.0, self.t_foto) + (n - 1) * self.transicion_efectiva

    def inicio(self, i: int) -> float:
        """Instante en que la foto ``i`` empieza a verse sola."""
        return i * self.paso

    def estado(self, t: float, n: int) -> tuple[int, float]:
        """Devuelve ``(i, alfa)``: se ve ``(1-alfa)·foto[i] + alfa·foto[i+1]``."""
        if n <= 0:
            raise ValueError("secuencia vacía")
        paso = self.paso
        if n == 1 or paso <= 0 or t <= 0:
            return 0, 0.0
        if t >= self.duracion(n):
            return n - 1, 0.0
        i = min(int(t // paso), n - 1)
        resto = t - i * paso
        t_foto = max(0.0, self.t_foto)
        if resto < t_foto or i == n - 1:
            return i, 0.0
        return i, min(1.0, (resto - t_foto) / self.transicion_efectiva)
