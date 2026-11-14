"""Preferencias que se guardan entre sesiones de la ventana (alineación, ritmo, vídeo, vista).

Se guardan con QSettings en ``~/.config/Evoluciona/Evoluciona.ini`` (formato INI). Los valores leídos se
validan: si un valor no es válido (fichero editado a mano, versión anterior...), se usa el de por defecto.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QByteArray, QSettings

from .alineacion import METODOS
from .linea_tiempo import Ritmo
from .proyecto import Opciones
from .segmentacion import MODELOS
from .video import CALIDADES, FORMATOS


class Configuracion:
    def __init__(self, ajustes: QSettings | None = None):
        self.q = ajustes or QSettings(QSettings.IniFormat, QSettings.UserScope, "Evoluciona", "Evoluciona")

    @property
    def fichero(self) -> str:
        return self.q.fileName()

    # ------------------------------------------------------------------ lectura con validación

    def _texto(self, clave: str, validos, defecto: str) -> str:
        v = self.q.value(clave)
        return v if isinstance(v, str) and v in validos else defecto

    def _real(self, clave: str, defecto: float, minimo: float, maximo: float) -> float:
        try:
            v = float(self.q.value(clave, defecto))
        except (TypeError, ValueError):
            return defecto
        return v if minimo <= v <= maximo else defecto

    def _logico(self, clave: str, defecto: bool) -> bool:
        v = self.q.value(clave)
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.lower() in ("true", "false"):
            return v.lower() == "true"
        return defecto

    def _entero_opcional(self, clave: str, defecto: int | None) -> int | None:
        v = self.q.value(clave)
        if v in (None, ""):
            return defecto
        if v == "original":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return defecto

    # ------------------------------------------------------------------ alineación

    def opciones(self, base: Opciones = Opciones()) -> Opciones:
        return Opciones(
            metodo=self._texto("alineacion/metodo", METODOS, base.metodo),
            referencia=self._texto("alineacion/referencia", ("anterior", "primera"), base.referencia),
            modelo=self._texto("alineacion/modelo", tuple(MODELOS), base.modelo),
            recortar=self._logico("alineacion/recortar", base.recortar),
        )

    def guardar_opciones(self, o: Opciones) -> None:
        self.q.setValue("alineacion/metodo", o.metodo)
        self.q.setValue("alineacion/referencia", o.referencia)
        self.q.setValue("alineacion/modelo", o.modelo)
        self.q.setValue("alineacion/recortar", o.recortar)

    # ------------------------------------------------------------------ ritmo (modo vídeo y exportación)

    def ritmo(self, base: Ritmo = Ritmo()) -> Ritmo:
        return Ritmo(
            t_foto=self._real("ritmo/t_foto", base.t_foto, 0.0, 60.0),
            t_transicion=self._real("ritmo/t_transicion", base.t_transicion, 0.05, 60.0),
            fundido=self._logico("ritmo/fundido", base.fundido),
        )

    def guardar_ritmo(self, r: Ritmo) -> None:
        self.q.setValue("ritmo/t_foto", r.t_foto)
        self.q.setValue("ritmo/t_transicion", r.t_transicion)
        self.q.setValue("ritmo/fundido", r.fundido)

    # ------------------------------------------------------------------ generación de vídeo

    def video(self) -> dict:
        """Formato, altura (None = original), fps y calidad elegidos la última vez."""
        return {
            "formato": self._texto("video/formato", tuple(FORMATOS), "mp4"),
            "alto": self._entero_opcional("video/alto", 1080),
            "fps": self._real("video/fps", 30.0, 1.0, 240.0),
            "calidad": self._texto("video/calidad", tuple(CALIDADES), "alta"),
        }

    def guardar_video(self, formato: str, alto: int | None, fps: float, calidad: str) -> None:
        self.q.setValue("video/formato", formato)
        self.q.setValue("video/alto", "original" if alto is None else int(alto))
        self.q.setValue("video/fps", fps)
        self.q.setValue("video/calidad", calidad)

    # ------------------------------------------------------------------ vista y ventana

    def logico(self, clave: str, defecto: bool) -> bool:
        return self._logico(clave, defecto)

    def guardar(self, clave: str, valor) -> None:
        self.q.setValue(clave, valor)

    def texto(self, clave: str, defecto: str = "") -> str:
        v = self.q.value(clave)
        return v if isinstance(v, str) else defecto

    def geometria(self) -> QByteArray | None:
        v = self.q.value("ventana/geometria")
        return v if isinstance(v, QByteArray) and not v.isEmpty() else None

    def sincronizar(self) -> None:
        self.q.sync()


def combinar(guardadas, explicitas: dict):
    """Aplica sobre lo guardado (Opciones o Ritmo) lo indicado expresamente en la línea de órdenes."""
    return replace(guardadas, **explicitas) if explicitas else guardadas
