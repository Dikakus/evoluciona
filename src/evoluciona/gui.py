"""Ventana principal: comparación por pares, modo vídeo y exportación."""

from __future__ import annotations

import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import (
    QElapsedTimer, QLibraryInfo, QObject, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, QTranslator, QUrl, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QDesktopServices, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressDialog, QPushButton, QSlider,
    QStackedWidget, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from . import version_completa
from .configuracion import Configuracion, combinar
from .entrada import EXTENSIONES, ErrorEntrada, ordenar_por_nombre, recopilar
from .linea_tiempo import Ritmo
from .proyecto import CacheFotogramas, Cancelado, Opciones, Proyecto, analizar, contornos
from .video import CALIDADES, FORMATOS, AjustesVideo, exportar, ffmpeg_disponible

LADO_PREVIA = 1280  # lado mayor de los fotogramas de previsualización
PASOS = 1000  # resolución del deslizador de transición
CONTORNO_ATENUADO = 0.5  # opacidad del contorno de la foto que se ve sola (0 % o 100 % del deslizador)


def opacidades_contornos(alfa: float) -> tuple[float, float]:
    """Opacidad de los contornos de A y B según la mezcla (0 = solo A, 1 = solo B).

    Ambos plenos al 50 %; en los extremos, el de la foto visible atenuado y el de la otra, oculto.
    """
    alfa = min(1.0, max(0.0, alfa))

    def una(peso: float) -> float:  # peso de la foto en la mezcla (1 = se ve sola)
        if peso >= 0.5:
            return 1.0 - (1.0 - CONTORNO_ATENUADO) * (peso - 0.5) / 0.5
        return peso / 0.5

    return una(1.0 - alfa), una(alfa)
MAX_ALTA = 4  # fotos a resolución completa que se guardan para ver con zoom

AYUDA = """
<h3>Navegación</h3>
<table cellspacing="4">
<tr><td><b>← / →</b></td><td>Par anterior / siguiente (en modo vídeo, foto anterior / siguiente)</td></tr>
<tr><td><b>Ctrl+G</b></td><td>Ir directamente a una foto (también con la lista «Ir a la foto»)</td></tr>
<tr><td><b>Espacio</b></td><td>Reproducir / pausar (modo vídeo)</td></tr>
<tr><td><b>Ctrl+O</b></td><td>Abrir fotos</td></tr>
</table>
<h3>Zoom</h3>
<table cellspacing="4">
<tr><td><b>Rueda del ratón</b></td><td>Acercar / alejar sobre el punto señalado</td></tr>
<tr><td><b>+ / −</b> (o Ctrl++ / Ctrl+−)</td><td>Acercar / alejar sobre el centro</td></tr>
<tr><td><b>0</b> (o Ctrl+0)</td><td>Ajustar a la ventana</td></tr>
<tr><td><b>Doble clic</b></td><td>Ampliar en ese punto / volver a ajustar</td></tr>
<tr><td><b>Arrastrar</b> (botón izquierdo)</td><td>Mover la imagen ampliada</td></tr>
<tr><td><b>Mayús + flechas</b></td><td>Mover la imagen ampliada</td></tr>
</table>
<h3>Comparación</h3>
<table cellspacing="4">
<tr><td><b>C</b></td><td>Mostrar u ocultar el contorno de las siluetas que se comparan (colores en el panel)</td></tr>
</table>
<p>El zoom se conserva al cambiar de par o de foto. Al ampliar, las fotos se vuelven a generar
a resolución completa en segundo plano.</p>
<p><b>F1</b>: esta ayuda.</p>
"""
LEYENDA = ("Zoom: rueda o + / −  ·  Mover: arrastrar o Mayús+flechas  ·  Ajustar: doble clic o 0  ·  "
           "Ir a una foto: Ctrl+G  ·  Ayuda: F1")


def _mmss(segundos: float) -> str:
    s = max(0, int(segundos + 0.0001))
    return f"{s // 60}:{s % 60:02d}"


class Visor(QWidget):
    """Dibuja la foto A y, encima, la foto B con la opacidad indicada. Admite zoom y desplazamiento."""

    zoom_cambiado = Signal(float)
    ZOOM_MAX = 16.0
    PASO_ZOOM = 1.25

    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(Qt.ClickFocus)  # al pinchar en la imagen, los atajos vuelven a la ventana
        self._a: tuple[int, QImage] | None = None
        self._b: tuple[int, QImage] | None = None
        self._alfa = 0.0
        self._escaladas: dict[tuple[int, int, int], QPixmap] = {}
        self._generacion = 0
        self._zoom = 1.0  # 1 = ajustado a la ventana
        self._cx = self._cy = 0.5  # punto de la imagen (0-1) situado en el centro del visor
        self._arrastre: QPointF | None = None
        self.rotulo = ""  # texto sobreimpresionado (nombre del fichero en modo vídeo)
        # contornos de siluetas: (color, [puntos (x, y) normalizados 0-1 sobre la imagen])
        self.contornos: list[tuple[QColor, list, float]] = []  # (color, polígonos, opacidad)
        self.mensaje = "Abre unas fotografías para empezar"

    def sizeHint(self) -> QSize:
        return QSize(900, 620)

    @property
    def zoom(self) -> float:
        return self._zoom

    def vaciar(self) -> None:
        self._a = self._b = None
        self._escaladas.clear()
        self._generacion += 1  # invalida las escaladas de un proyecto anterior
        self.rotulo = ""
        self.contornos = []
        self.ajustar()
        self.update()

    def indices(self) -> set[int]:
        """Índices de las fotos que se están mostrando."""
        return {x[0] for x in (self._a, self._b) if x is not None}

    def mostrar(self, a: tuple[int, QImage], b: tuple[int, QImage] | None, alfa: float) -> None:
        self._a, self._b, self._alfa = a, b, min(1.0, max(0.0, alfa))
        self.update()

    # ------------------------------------------------------------ geometría y zoom

    def _ajuste(self) -> tuple[float, float]:
        """Tamaño (en píxeles lógicos) de la imagen ajustada a la ventana."""
        img = self._a[1]
        f = min(self.width() / img.width(), self.height() / img.height())
        return max(1.0, img.width() * f), max(1.0, img.height() * f)

    def _destino(self) -> QRectF:
        fw, fh = self._ajuste()
        w, h = fw * self._zoom, fh * self._zoom
        return QRectF(self.width() / 2 - self._cx * w, self.height() / 2 - self._cy * h, w, h)

    def _acotar(self) -> None:
        """Impide desplazar la imagen más allá de sus bordes."""
        if self._a is None:
            return
        fw, fh = self._ajuste()
        mx = min(0.5, self.width() / (2 * fw * self._zoom))
        my = min(0.5, self.height() / (2 * fh * self._zoom))
        self._cx = min(max(self._cx, mx), 1 - mx)
        self._cy = min(max(self._cy, my), 1 - my)

    def ancho_necesario(self) -> float:
        """Anchura en píxeles de pantalla que ocuparía la imagen completa con el zoom actual."""
        if self._a is None:
            return 0.0
        return self._ajuste()[0] * self._zoom * self.devicePixelRatioF()

    def fijar_zoom(self, zoom: float, ancla: QPointF | None = None) -> None:
        """Cambia el zoom manteniendo fijo el punto de la imagen situado bajo ``ancla``."""
        zoom = min(max(zoom, 1.0), self.ZOOM_MAX)
        if self._a is None or abs(zoom - self._zoom) < 1e-9:
            return
        if ancla is None:
            ancla = QPointF(self.width() / 2, self.height() / 2)
        d = self._destino()
        u, v = (ancla.x() - d.x()) / d.width(), (ancla.y() - d.y()) / d.height()
        self._zoom = zoom
        fw, fh = self._ajuste()
        self._cx = u + (self.width() / 2 - ancla.x()) / (fw * zoom)
        self._cy = v + (self.height() / 2 - ancla.y()) / (fh * zoom)
        self._acotar()
        self.update()
        self.zoom_cambiado.emit(self._zoom)

    def acercar(self) -> None:
        self.fijar_zoom(self._zoom * self.PASO_ZOOM)

    def alejar(self) -> None:
        self.fijar_zoom(self._zoom / self.PASO_ZOOM)

    def ajustar(self) -> None:
        """Vuelve a mostrar la imagen completa."""
        cambia = self._zoom != 1.0
        self._zoom, self._cx, self._cy = 1.0, 0.5, 0.5
        self.update()
        if cambia:
            self.zoom_cambiado.emit(1.0)

    def desplazar(self, dx: float, dy: float) -> None:
        """Mueve la vista una fracción (dx, dy) del tamaño del visor."""
        if self._a is None or self._zoom <= 1.0:
            return
        d = self._destino()
        self._cx += dx * self.width() / d.width()
        self._cy += dy * self.height() / d.height()
        self._acotar()
        self.update()

    def wheelEvent(self, evento) -> None:
        pasos = evento.angleDelta().y() / 120.0
        if self._a is None or pasos == 0:
            evento.ignore()
            return
        self.fijar_zoom(self._zoom * self.PASO_ZOOM ** pasos, evento.position())
        evento.accept()

    def mousePressEvent(self, evento) -> None:
        if evento.button() == Qt.LeftButton and self._a is not None and self._zoom > 1.0:
            self._arrastre = evento.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, evento) -> None:
        if self._arrastre is None:
            return
        d = self._destino()
        delta = evento.position() - self._arrastre
        self._arrastre = evento.position()
        self._cx -= delta.x() / d.width()
        self._cy -= delta.y() / d.height()
        self._acotar()
        self.update()

    def mouseReleaseEvent(self, evento) -> None:
        if evento.button() == Qt.LeftButton and self._arrastre is not None:
            self._arrastre = None
            self.unsetCursor()

    def mouseDoubleClickEvent(self, evento) -> None:
        if evento.button() != Qt.LeftButton or self._a is None:
            return
        if self._zoom > 1.0:
            self.ajustar()
        else:
            self.fijar_zoom(2.5, evento.position())

    def resizeEvent(self, evento) -> None:
        self._acotar()
        super().resizeEvent(evento)

    # ------------------------------------------------------------ dibujo

    def _escalada(self, entrada: tuple[int, QImage], tam: QSize) -> QPixmap:
        clave = (self._generacion, entrada[0], tam.width() * 100000 + tam.height())
        pm = self._escaladas.get(clave)
        if pm is None:
            if len(self._escaladas) >= 6:
                self._escaladas.clear()
            pm = QPixmap.fromImage(entrada[1].scaled(tam, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            self._escaladas[clave] = pm
        return pm

    def paintEvent(self, _evento) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(24, 24, 24))
        if self._a is None:
            p.setPen(QColor(170, 170, 170))
            p.drawText(self.rect(), Qt.AlignCenter, self.mensaje)
            return
        ver_a = self._alfa < 1.0 or self._b is None
        ver_b = self._b is not None and self._alfa > 0.0
        d = self._destino()
        if self._zoom == 1.0:
            # sin zoom se dibujan copias ya escaladas: es lo más fluido al reproducir
            destino = QRect(round(d.x()), round(d.y()), max(1, int(d.width())), max(1, int(d.height())))
            dpr = self.devicePixelRatioF()
            tam = QSize(max(1, round(destino.width() * dpr)), max(1, round(destino.height() * dpr)))
            if ver_a:
                p.drawPixmap(destino, self._escalada(self._a, tam))
            if ver_b:
                p.setOpacity(self._alfa)
                p.drawPixmap(destino, self._escalada(self._b, tam))
            visible = QRectF(destino)
        else:
            # con zoom solo se escala la parte visible
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            visible = d.intersected(QRectF(self.rect()))

            def dibujar(img: QImage) -> None:
                sx, sy = img.width() / d.width(), img.height() / d.height()
                origen = QRectF((visible.x() - d.x()) * sx, (visible.y() - d.y()) * sy,
                                visible.width() * sx, visible.height() * sy)
                p.drawImage(visible, img, origen)

            if ver_a:
                dibujar(self._a[1])
            if ver_b:
                p.setOpacity(self._alfa)
                dibujar(self._b[1])
        p.setOpacity(1.0)
        if self.contornos:
            self._dibujar_contornos(p, d, visible)
        if self.rotulo:
            self._dibujar_rotulo(p, visible)

    def _dibujar_contornos(self, p: QPainter, d: QRectF, zona: QRectF) -> None:
        """Contornos en coordenadas normalizadas: siguen al zoom y al desplazamiento."""
        p.save()
        p.setClipRect(zona)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(Qt.NoBrush)
        for color, poligonos, opacidad in self.contornos:
            if opacidad <= 0.0 or not poligonos:
                continue
            p.setOpacity(opacidad)
            camino = QPainterPath()
            for puntos in poligonos:
                xs = d.x() + puntos[:, 0] * d.width()
                ys = d.y() + puntos[:, 1] * d.height()
                camino.moveTo(xs[0], ys[0])
                for x, y in zip(xs[1:], ys[1:]):
                    camino.lineTo(x, y)
                camino.closeSubpath()
            sombra = QPen(QColor(0, 0, 0, 110), 4.0)  # borde oscuro: se ve también sobre fondos claros
            sombra.setCosmetic(True)
            p.setPen(sombra)
            p.drawPath(camino)
            lapiz = QPen(color, 2.0)
            lapiz.setCosmetic(True)
            p.setPen(lapiz)
            p.drawPath(camino)
        p.restore()

    def _dibujar_rotulo(self, p: QPainter, zona: QRectF) -> None:
        fuente = p.font()
        fuente.setBold(True)
        fuente.setPixelSize(max(13, round(self.height() / 32)))
        p.setFont(fuente)
        margen = fuente.pixelSize() * 0.6
        texto = p.fontMetrics().elidedText(self.rotulo, Qt.ElideMiddle, max(20, int(zona.width() - 4 * margen)))
        ancho = p.fontMetrics().horizontalAdvance(texto) + 2 * margen
        alto = p.fontMetrics().height() + margen
        caja = QRectF(zona.left() + margen, zona.bottom() - margen - alto, ancho, alto)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(caja, margen / 2, margen / 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(caja, Qt.AlignCenter, texto)


class _Puente(QObject):
    """Lleva al hilo de la interfaz las fotos generadas a resolución completa."""

    listo = Signal(int, int, object)  # generación, índice, ndarray (o None si falló)


class HiloAnalisis(QThread):
    progreso = Signal(int, int, str)
    listo = Signal(object)
    fallo = Signal(str)

    def __init__(self, rutas: list[Path], opciones: Opciones, padre=None):
        super().__init__(padre)
        self.rutas, self.opciones = rutas, opciones

    def run(self) -> None:
        try:
            proyecto = analizar(self.rutas, self.opciones, progreso=self.progreso.emit,
                                cancelado=self.isInterruptionRequested)
        except Cancelado:
            self.fallo.emit("")
        except Exception as e:  # se muestra al usuario
            self.fallo.emit(str(e) or type(e).__name__)
        else:
            self.listo.emit(proyecto)


class HiloExportacion(QThread):
    progreso = Signal(int, int)
    listo = Signal(str)
    fallo = Signal(str)

    def __init__(self, proyecto: Proyecto, salida: Path, ajustes: AjustesVideo, padre=None):
        super().__init__(padre)
        self.proyecto, self.salida, self.ajustes = proyecto, salida, ajustes

    def run(self) -> None:
        try:
            escrito = exportar(self.proyecto, self.salida, self.ajustes, progreso=self.progreso.emit,
                               cancelado=self.isInterruptionRequested)
        except Cancelado:
            self.fallo.emit("")
        except Exception as e:
            self.fallo.emit(str(e) or type(e).__name__)
        else:
            self.listo.emit(str(escrito))


def _combo(opciones: list[tuple[str, object]], actual=None) -> QComboBox:
    c = QComboBox()
    for texto, dato in opciones:
        c.addItem(texto, dato)
    if actual is not None:
        i = c.findData(actual)
        if i >= 0:
            c.setCurrentIndex(i)
    return c


def _segundos(valor: float, minimo: float = 0.0) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(minimo, 60.0)
    s.setDecimals(2)
    s.setSingleStep(0.25)
    s.setSuffix(" s")
    s.setValue(valor)
    return s


class DialogoAlineacion(QDialog):
    def __init__(self, opciones: Opciones, padre=None):
        super().__init__(padre)
        self.setWindowTitle("Opciones de alineación")
        self.metodo = _combo([
            ("Contorno: máximo solape de las siluetas", "contorno"),
            ("Altura: iguala la altura de la figura", "altura"),
            ("Área: iguala la superficie de la figura", "area"),
            ("Ninguno: sin alinear", "ninguno"),
        ], opciones.metodo)
        self.referencia = _combo([
            ("La foto anterior (evolución gradual)", "anterior"),
            ("Siempre la primera foto", "primera"),
        ], opciones.referencia)
        self.modelo = _combo([
            ("General: cualquier figura destacada", "general"),
            ("Persona: específico para personas", "persona"),
            ("Rápido: menos preciso", "rapido"),
        ], opciones.modelo)
        self.recortar = QCheckBox("Recortar a la zona común a todas las fotos")
        self.recortar.setChecked(opciones.recortar)
        form = QFormLayout(self)
        form.addRow("Método:", self.metodo)
        form.addRow("Alinear con:", self.referencia)
        form.addRow("Detección de la figura:", self.modelo)
        form.addRow(self.recortar)
        botones = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        form.addRow(botones)

    def opciones(self) -> Opciones:
        return Opciones(self.metodo.currentData(), self.referencia.currentData(), self.modelo.currentData(),
                        self.recortar.isChecked())


class DialogoExportar(QDialog):
    def __init__(self, proyecto: Proyecto, ritmo: Ritmo, salida: Path, padre=None, preferidos: dict | None = None):
        super().__init__(padre)
        self.setWindowTitle("Generar vídeo")
        self.proyecto = proyecto
        alto_original = proyecto.tam_salida(par=True)[1]
        pref = preferidos or {}  # lo elegido la última vez (configuración guardada)

        self.formato = _combo([(f.nombre, clave) for clave, f in FORMATOS.items()], pref.get("formato", "mp4"))
        resoluciones = [(f"Original ({alto_original} px de alto)", None)]
        resoluciones += [(f"{a} px de alto", a) for a in (2160, 1440, 1080, 720, 480, 360) if a < alto_original]
        self.alto = _combo(resoluciones, 1080 if alto_original > 1080 else None)
        if "alto" in pref:  # la altura guardada solo si cabe en este proyecto (si no, la original)
            i = self.alto.findData(pref["alto"])
            self.alto.setCurrentIndex(i if i >= 0 else 0)
        self.fps = _combo([(f"{v} fps", float(v)) for v in (60, 50, 30, 25, 24, 15, 10)], pref.get("fps", 30.0))
        self.calidad = _combo([(c.capitalize(), c) for c in CALIDADES], pref.get("calidad", "alta"))
        self.calidad.setEnabled(self.formato.currentData() != "gif")
        self.transicion = _combo([("Fundido entre fotos", True), ("Sin fundido (corte)", False)], ritmo.fundido)
        self.t_foto = _segundos(ritmo.t_foto)
        self.t_transicion = _segundos(ritmo.t_transicion, 0.05)
        self.fichero = QLineEdit(str(salida))
        examinar = QPushButton("Examinar…")
        examinar.clicked.connect(self._examinar)
        fila = QHBoxLayout()
        fila.addWidget(self.fichero, 1)
        fila.addWidget(examinar)
        self.resumen = QLabel()

        form = QFormLayout(self)
        form.addRow("Formato:", self.formato)
        form.addRow("Resolución:", self.alto)
        form.addRow("Fluidez:", self.fps)
        form.addRow("Calidad:", self.calidad)
        form.addRow("Transición:", self.transicion)
        form.addRow("Tiempo por foto:", self.t_foto)
        form.addRow("Duración del fundido:", self.t_transicion)
        form.addRow("Guardar en:", fila)
        form.addRow(self.resumen)
        self.botones = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.botones.button(QDialogButtonBox.Ok).setText("Generar")
        self.botones.accepted.connect(self._aceptar)
        self.botones.rejected.connect(self.reject)
        form.addRow(self.botones)
        self.setMinimumWidth(520)

        self.formato.currentIndexChanged.connect(self._formato_cambiado)
        for w in (self.alto, self.fps, self.transicion):
            w.currentIndexChanged.connect(self._actualizar)
        for w in (self.t_foto, self.t_transicion):
            w.valueChanged.connect(self._actualizar)
        self._confirmada: str | None = None  # ruta ya confirmada en el selector de ficheros
        self._actualizar()

    def ritmo(self) -> Ritmo:
        return Ritmo(self.t_foto.value(), self.t_transicion.value(), bool(self.transicion.currentData()))

    def ajustes(self) -> AjustesVideo:
        return AjustesVideo(self.formato.currentData(), self.fps.currentData(), self.alto.currentData(),
                            self.calidad.currentData(), self.ritmo())

    def salida(self) -> Path:
        ext = FORMATOS[self.formato.currentData()].extension
        return Path(self.fichero.text().strip()).expanduser().with_suffix(ext)

    def _formato_cambiado(self) -> None:
        es_gif = self.formato.currentData() == "gif"
        self.calidad.setEnabled(not es_gif)
        if es_gif:  # un GIF a tamaño completo y 30 fps es enorme
            for combo, dato in ((self.fps, 15.0), (self.alto, 480)):
                i = combo.findData(dato)
                if i >= 0:
                    combo.setCurrentIndex(i)
        if self.fichero.text().strip():
            self.fichero.setText(str(self.salida()))
        self._actualizar()

    def _actualizar(self) -> None:
        r = self.ritmo()
        self.t_transicion.setEnabled(r.fundido)
        w, h = self.proyecto.tam_salida(alto=self.alto.currentData(), par=True)
        dur = r.duracion(len(self.proyecto))
        self.resumen.setText(f"{len(self.proyecto)} fotos · {w}×{h} px · duración {_mmss(dur)} "
                             f"({round(dur * self.fps.currentData())} fotogramas)")
        self.botones.button(QDialogButtonBox.Ok).setEnabled(dur > 0)

    def _examinar(self) -> None:
        fmt = FORMATOS[self.formato.currentData()]
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar vídeo", self.fichero.text(),
                                              f"Vídeo (*{fmt.extension})")
        if ruta:
            self.fichero.setText(ruta)
            self.fichero.setText(str(self.salida()))
            self._confirmada = self.fichero.text()

    def _aceptar(self) -> None:
        if not self.fichero.text().strip():
            QMessageBox.warning(self, "Generar vídeo", "Indica el fichero de salida.")
            return
        salida = self.salida()
        if not salida.parent.is_dir():
            QMessageBox.warning(self, "Generar vídeo", f"No existe la carpeta:\n{salida.parent}")
            return
        if salida.exists() and str(salida) != self._confirmada:
            if QMessageBox.question(self, "Generar vídeo", f"Ya existe:\n{salida}\n\n¿Sobrescribirlo?") \
                    != QMessageBox.Yes:
                return
        self.accept()


class Ventana(QMainWindow):
    def __init__(self, opciones: Opciones, ritmo: Ritmo, orden: str = "nombre", config: Configuracion | None = None):
        super().__init__()
        self.setWindowTitle("Evoluciona")
        self.opciones, self.ritmo, self.orden = opciones, ritmo, orden
        self.config = config or Configuracion()
        self.proyecto: Proyecto | None = None
        self.cache: CacheFotogramas | None = None
        self._qimagenes: dict[int, QImage] = {}
        self._contornos: dict[int, list] = {}
        self._par = 0
        self._t = 0.0  # posición en modo vídeo (s)
        self._hilo: QThread | None = None
        self._dialogo_progreso: QProgressDialog | None = None
        # fotos a resolución completa para el zoom (la caché normal es de LADO_PREVIA)
        self._alta: dict[int, QImage] = {}
        self._pedidas: set[int] = set()
        self._generacion = 0  # cambia con cada proyecto: descarta resultados de uno anterior
        self._puente = _Puente(self)
        self._puente.listo.connect(self._alta_lista)
        self._ejecutor_alta = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alta")
        self._espera_alta = QTimer(self)
        self._espera_alta.setSingleShot(True)
        self._espera_alta.setInterval(250)
        self._espera_alta.timeout.connect(self._pedir_alta)

        self.visor = Visor()
        self.visor.zoom_cambiado.connect(self._zoom_cambiado)
        self.titulo = QLabel()
        self.titulo.setAlignment(Qt.AlignCenter)

        self._crear_acciones()
        self.paneles = QStackedWidget()
        self.paneles.addWidget(self._crear_panel_pares())
        self.paneles.addWidget(self._crear_panel_video())

        central = QWidget()
        capa = QVBoxLayout(central)
        capa.addWidget(self.visor, 1)
        capa.addWidget(self.titulo)
        capa.addWidget(self.paneles)
        self.setCentralWidget(central)

        self.reloj = QTimer(self)
        self.reloj.setInterval(16)
        self.reloj.timeout.connect(self._tic)
        self.cronometro = QElapsedTimer()

        QShortcut(QKeySequence(Qt.Key_Left), self, self._anterior)
        QShortcut(QKeySequence(Qt.Key_Right), self, self._siguiente)
        QShortcut(QKeySequence(Qt.Key_Space), self, self._alternar_reproduccion)
        for tecla, dx, dy in (("Shift+Left", -0.1, 0), ("Shift+Right", 0.1, 0),
                              ("Shift+Up", 0, -0.1), ("Shift+Down", 0, 0.1)):
            QShortcut(QKeySequence(tecla), self, lambda dx=dx, dy=dy: self.visor.desplazar(dx, dy))

        self._crear_barra_estado()
        self._actualizar_controles()

    # ---------------------------------------------------------------- construcción

    def _crear_acciones(self) -> None:
        barra = self.addToolBar("Principal")
        barra.setMovable(False)
        barra.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        est = self.style()

        def accion(texto, icono, ranura, atajo=None) -> QAction:
            a = QAction(est.standardIcon(icono), texto, self)
            a.triggered.connect(ranura)
            if atajo:
                a.setShortcut(atajo)
            barra.addAction(a)
            return a

        act_abrir = accion("Abrir fotos…", QStyle.SP_FileIcon, self._abrir_fotos, QKeySequence.Open)
        act_carpeta = accion("Abrir carpeta…", QStyle.SP_DirOpenIcon, self._abrir_carpeta)
        self.act_alineacion = accion("Alineación…", QStyle.SP_FileDialogDetailedView, self._cambiar_alineacion)
        barra.addSeparator()
        self.act_video = accion("Modo vídeo", QStyle.SP_MediaPlay, self._cambiar_modo)
        self.act_video.setCheckable(True)
        self.act_exportar = accion("Generar vídeo…", QStyle.SP_DialogSaveButton, self._exportar)
        barra.addSeparator()

        # selector para ir directamente a una foto
        barra.addWidget(QLabel(" Ir a la foto: "))
        self.selector = QComboBox()
        self.selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.selector.setMinimumContentsLength(24)
        self.selector.setMaxVisibleItems(25)
        self.selector.setFocusPolicy(Qt.ClickFocus)
        self.selector.setToolTip("Ir directamente a una foto (Ctrl+G). Con la lista abierta, "
                                 "escribe el número de la foto para saltar a ella.")
        self.selector.activated.connect(self._ir_a_foto)
        barra.addWidget(self.selector)

        def nueva(texto, ranura, atajos=(), checkable=False) -> QAction:
            a = QAction(texto, self)
            a.triggered.connect(ranura)
            a.setShortcuts([QKeySequence(t) for t in atajos])
            a.setCheckable(checkable)
            return a

        self.act_acercar = nueva("Acercar", self.visor.acercar, ("+", "Ctrl++", "=", "Ctrl+="))
        self.act_alejar = nueva("Alejar", self.visor.alejar, ("-", "Ctrl+-"))
        self.act_ajustar = nueva("Ajustar a la ventana", self.visor.ajustar, ("0", "Ctrl+0"))
        self.act_ir = nueva("Ir a la foto…", self._abrir_selector, ("Ctrl+G",))
        self.act_rotulo = nueva("Nombre del fichero en modo vídeo", self._refrescar, checkable=True)
        self.act_rotulo.setChecked(self.config.logico("vista/rotulo", True))
        self.act_rotulo.toggled.connect(lambda v: self.config.guardar("vista/rotulo", v))

        menus = self.menuBar()
        m = menus.addMenu("&Archivo")
        m.addActions([act_abrir, act_carpeta])
        m.addSeparator()
        m.addAction(self.act_exportar)
        m.addSeparator()
        m.addAction(nueva("Salir", self.close, ("Ctrl+Q",)))
        m = menus.addMenu("&Ver")
        m.addActions([self.act_video, self.act_ir])
        m.addSeparator()
        m.addActions([self.act_acercar, self.act_alejar, self.act_ajustar])
        m.addSeparator()
        m.addAction(self.act_rotulo)
        self.act_contornos = nueva("Contornos de las siluetas (pares)", lambda: self.chk_contornos.toggle(), ("C",))
        m.addAction(self.act_contornos)
        m = menus.addMenu("A&lineación")
        m.addAction(self.act_alineacion)
        m = menus.addMenu("Ay&uda")
        m.addAction(nueva("Atajos de teclado y ratón", self._mostrar_ayuda, ("F1",)))
        m.addAction(nueva("Acerca de Evoluciona", self._acerca_de))

    def _crear_barra_estado(self) -> None:
        barra = self.statusBar()
        leyenda = QLabel(LEYENDA)
        leyenda.setMinimumWidth(10)  # puede recortarse si la ventana es estrecha
        barra.addWidget(leyenda, 1)
        for accion, texto in ((self.act_alejar, "−"), (self.act_acercar, "+"), (self.act_ajustar, "Ajustar")):
            # el botón toma de la acción el texto corto (iconText) y la descripción (toolTip)
            accion.setIconText(texto)
            accion.setToolTip(f"{accion.text()} ({accion.shortcut().toString(QKeySequence.NativeText)})")
            boton = QToolButton()
            boton.setDefaultAction(accion)
            boton.setAutoRaise(True)
            boton.setFocusPolicy(Qt.NoFocus)
            barra.addPermanentWidget(boton)
        self.lbl_zoom = QLabel()
        self.lbl_zoom.setMinimumWidth(110)
        self.lbl_zoom.setAlignment(Qt.AlignCenter)
        barra.addPermanentWidget(self.lbl_zoom)
        self._zoom_cambiado(self.visor.zoom)

    def _crear_panel_pares(self) -> QWidget:
        est = self.style()
        self.btn_anterior = QPushButton(est.standardIcon(QStyle.SP_ArrowLeft), "Anterior")
        self.btn_siguiente = QPushButton(est.standardIcon(QStyle.SP_ArrowRight), "Siguiente")
        self.btn_siguiente.setLayoutDirection(Qt.RightToLeft)  # icono a la derecha del texto
        self.btn_anterior.setToolTip("Par anterior (←)")
        self.btn_siguiente.setToolTip("Par siguiente (→)")
        self.btn_anterior.clicked.connect(self._anterior)
        self.btn_siguiente.clicked.connect(self._siguiente)
        self.nombre_a, self.nombre_b = QLabel(), QLabel()
        self.deslizador = QSlider(Qt.Horizontal)
        self.deslizador.setRange(0, PASOS)
        self.deslizador.setPageStep(PASOS // 10)
        self.deslizador.setToolTip("Transición de la primera foto del par a la segunda")
        self.deslizador.valueChanged.connect(self._refrescar)
        self.superponer = QCheckBox("Superponer ambas al 50 %")
        self.superponer.setToolTip("Muestra las dos fotos del par a la vez, con la mitad de opacidad cada una")
        self.superponer.toggled.connect(self._superponer_cambiado)

        self.chk_contornos = QCheckBox("Contornos")
        self.chk_contornos.setToolTip("Dibuja el contorno de la silueta detectada en cada foto del par (C)")
        self.chk_contornos.setChecked(self.config.logico("vista/contornos", False))
        self.chk_contornos.toggled.connect(self._contornos_cambiado)
        self.colores = [self.config.color("vista/color_a", "#ff3030"), self.config.color("vista/color_b", "#30e030")]
        self.btn_colores = []
        for k, lado in enumerate(("izquierda (A)", "derecha (B)")):
            boton = QToolButton()
            boton.setToolTip(f"Color del contorno de la foto de la {lado}")
            boton.clicked.connect(lambda _=False, k=k: self._elegir_color(k))
            self.btn_colores.append(boton)
        fila2 = QHBoxLayout()
        fila2.addStretch(1)
        fila2.addWidget(self.superponer)
        fila2.addSpacing(24)
        fila2.addWidget(self.chk_contornos)
        for boton in self.btn_colores:
            fila2.addWidget(boton)
        fila2.addStretch(1)

        fila = QHBoxLayout()
        fila.addWidget(self.btn_anterior)
        fila.addWidget(self.nombre_a)
        fila.addWidget(self.deslizador, 1)
        fila.addWidget(self.nombre_b)
        fila.addWidget(self.btn_siguiente)
        panel = QWidget()
        capa = QVBoxLayout(panel)
        capa.setContentsMargins(0, 0, 0, 0)
        capa.addLayout(fila)
        capa.addLayout(fila2)
        self._pintar_colores()
        return panel

    def _crear_panel_video(self) -> QWidget:
        est = self.style()
        self.btn_inicio = QPushButton(est.standardIcon(QStyle.SP_MediaSkipBackward), "Inicio")
        self.btn_reproducir = QPushButton(est.standardIcon(QStyle.SP_MediaPlay), "Reproducir")
        self.btn_final = QPushButton(est.standardIcon(QStyle.SP_MediaSkipForward), "Final")
        self.btn_reproducir.setToolTip("Reproducir / pausar (espacio)")
        self.btn_inicio.clicked.connect(lambda: self._ir_a(0.0))
        self.btn_final.clicked.connect(self._ir_al_final)
        self.btn_reproducir.clicked.connect(self._alternar_reproduccion)
        self.linea = QSlider(Qt.Horizontal)
        self.linea.valueChanged.connect(self._linea_movida)
        self.tiempo = QLabel("0:00 / 0:00")

        self.cmb_transicion = _combo([("Fundido", True), ("Sin fundido", False)], self.ritmo.fundido)
        self.spn_foto = _segundos(self.ritmo.t_foto)
        self.spn_transicion = _segundos(self.ritmo.t_transicion, 0.05)
        self.cmb_transicion.currentIndexChanged.connect(self._ritmo_cambiado)
        self.spn_foto.valueChanged.connect(self._ritmo_cambiado)
        self.spn_transicion.valueChanged.connect(self._ritmo_cambiado)

        fila1 = QHBoxLayout()
        for w in (self.btn_inicio, self.btn_reproducir, self.btn_final):
            fila1.addWidget(w)
        fila1.addWidget(self.linea, 1)
        fila1.addWidget(self.tiempo)
        fila2 = QHBoxLayout()
        fila2.addStretch(1)
        for texto, w in (("Transición:", self.cmb_transicion), ("Tiempo por foto:", self.spn_foto),
                         ("Duración del fundido:", self.spn_transicion)):
            fila2.addWidget(QLabel(texto))
            fila2.addWidget(w)
            fila2.addSpacing(12)
        fila2.addStretch(1)
        panel = QWidget()
        capa = QVBoxLayout(panel)
        capa.setContentsMargins(0, 0, 0, 0)
        capa.addLayout(fila1)
        capa.addLayout(fila2)
        return panel

    # ---------------------------------------------------------------- estado

    @property
    def n(self) -> int:
        return len(self.proyecto) if self.proyecto else 0

    @property
    def modo_video(self) -> bool:
        return self.act_video.isChecked()

    def _actualizar_controles(self) -> None:
        hay, pares = self.n > 0, self.n > 1
        self.act_alineacion.setEnabled(hay)
        self.act_video.setEnabled(pares)
        self.act_exportar.setEnabled(hay)
        self.selector.setEnabled(pares)
        self.act_ir.setEnabled(pares)
        for a in (self.act_acercar, self.act_alejar, self.act_ajustar):
            a.setEnabled(hay)
        self.btn_anterior.setEnabled(pares and self._par > 0)
        self.btn_siguiente.setEnabled(pares and self._par < self.n - 2)
        self.superponer.setEnabled(pares)
        self.chk_contornos.setEnabled(pares)
        self.act_contornos.setEnabled(pares)
        for boton in self.btn_colores:
            boton.setEnabled(pares)
        self.deslizador.setEnabled(pares and not self.superponer.isChecked())
        self.spn_transicion.setEnabled(self.ritmo.fundido)
        for w in (self.btn_inicio, self.btn_reproducir, self.btn_final, self.linea):
            w.setEnabled(pares)

    def _qimagen(self, i: int) -> tuple[int, QImage]:
        q = self._alta.get(i)
        if q is None:
            q = self._qimagenes.get(i)
        if q is None:
            arr = self.cache.obtener(i)
            alto, ancho = arr.shape[:2]
            q = QImage(arr.data, ancho, alto, 3 * ancho, QImage.Format_RGB888).copy()
            if len(self._qimagenes) >= 6:
                self._qimagenes.clear()
            self._qimagenes[i] = q
        return i, q

    def _refrescar(self) -> None:
        """Dibuja lo que corresponde al modo y la posición actuales."""
        if not self.proyecto:
            self.visor.vaciar()
            self.titulo.setText("")
            return
        n = self.n
        if self.modo_video:
            i, alfa = self.ritmo.estado(self._t, n)
            dur = self.ritmo.duracion(n)
            self.tiempo.setText(f"{_mmss(self._t)} / {_mmss(dur)}")
            self.linea.blockSignals(True)
            self.linea.setValue(round(self._t * 1000))
            self.linea.blockSignals(False)
            actual = i + 1 if alfa > 0.5 and i + 1 < n else i  # la que predomina en el fundido
            self.titulo.setText(f"Foto {actual + 1} de {n} · {self.proyecto.rutas[actual].name}")
            self.visor.rotulo = self.proyecto.rutas[actual].name if self.act_rotulo.isChecked() else ""
        else:
            i = self._par
            alfa = 0.5 if self.superponer.isChecked() else self.deslizador.value() / PASOS
            if n < 2:
                alfa = 0.0
                self.titulo.setText(self.proyecto.rutas[0].name)
                self.nombre_a.setText("")
                self.nombre_b.setText("")
            else:
                self.titulo.setText(f"Par {i + 1} de {n - 1}")
                self.nombre_a.setText(self.proyecto.rutas[i].name)
                self.nombre_b.setText(self.proyecto.rutas[i + 1].name)
            actual = i + 1 if alfa > 0.5 else i
            self.visor.rotulo = ""  # en pares los nombres ya están junto al deslizador
            self.visor.contornos = []
            if self.chk_contornos.isChecked() and n >= 2:
                op_a, op_b = opacidades_contornos(alfa)  # se funden con su foto
                self.visor.contornos = [(self.colores[0], self._contornos_de(i), op_a),
                                        (self.colores[1], self._contornos_de(i + 1), op_b)]
        if self.modo_video:
            self.visor.contornos = []
        if self.selector.currentIndex() != actual:
            self.selector.setCurrentIndex(actual)
        try:
            a = self._qimagen(i)
            b = self._qimagen(i + 1) if i + 1 < n and (alfa > 0.0 or not self.modo_video) else None
        except Exception as e:
            self._pausar()
            self.visor.vaciar()
            self.titulo.setText(f"No se puede mostrar la foto: {e}")
            return
        self.visor.mostrar(a, b, alfa)
        for k in (i + 1, i + 2, i - 1):  # precarga de las fotos vecinas
            self.cache.precargar(k)
        if self.visor.zoom > 1.0 and not self.reloj.isActive():
            self._espera_alta.start()

    # ---------------------------------------------------------------- pares

    # ---------------------------------------------------------------- contornos

    def _contornos_de(self, i: int) -> list:
        """Contornos (normalizados) de la silueta de la foto ``i``, calculados una vez por proyecto."""
        if i not in self._contornos:
            sil = self.proyecto.silueta(i, self.cache.tam) if self.proyecto else None
            self._contornos[i] = contornos(sil) if sil is not None else []
        return self._contornos[i]

    def _contornos_cambiado(self, activo: bool) -> None:
        self.config.guardar("vista/contornos", activo)
        self._pintar_colores()
        self._refrescar()
        if activo and self.proyecto and self.n >= 2 and not (self._contornos_de(self._par)
                                                              or self._contornos_de(self._par + 1)):
            self.statusBar().showMessage("No hay siluetas para estas fotos (¿alineación «Ninguno»?).", 6000)

    def _elegir_color(self, k: int) -> None:
        lado = "izquierda (A)" if k == 0 else "derecha (B)"
        color = QColorDialog.getColor(self.colores[k], self, f"Color del contorno de la foto de la {lado}")
        if color.isValid():
            self.colores[k] = color
            self.config.guardar("vista/color_a" if k == 0 else "vista/color_b", color.name())
            self._pintar_colores()
            self._refrescar()
        self.activateWindow()

    def _pintar_colores(self) -> None:
        """Muestra el color en cada botón y, con los contornos activos, colorea el nombre de cada foto."""
        for boton, color in zip(self.btn_colores, self.colores):
            muestra = QPixmap(18, 18)
            muestra.fill(color)
            boton.setIcon(QIcon(muestra))
        activo = self.chk_contornos.isChecked()
        for etiqueta, color in zip((self.nombre_a, self.nombre_b), self.colores):
            etiqueta.setStyleSheet(f"color: {color.name()}; font-weight: bold;" if activo else "")

    def _reiniciar_deslizador(self) -> None:
        """Al principio del par, o en el centro si está marcada la superposición al 50 %."""
        self.deslizador.blockSignals(True)
        self.deslizador.setValue(PASOS // 2 if self.superponer.isChecked() else 0)
        self.deslizador.blockSignals(False)

    def _ir_a_par(self, par: int) -> None:
        if self.n < 2 or self.modo_video:
            return
        self._par = min(max(par, 0), self.n - 2)
        self._reiniciar_deslizador()
        self._actualizar_controles()
        self._refrescar()

    def _ir_a_foto(self, i: int) -> None:
        """Muestra la foto ``i``: en modo vídeo salta a ella; en pares, abre el par que empieza en ella."""
        if self.n < 2:
            return
        i = min(max(i, 0), self.n - 1)
        if self.modo_video:
            self._ir_a(self.ritmo.inicio(i))
        else:
            self._ir_a_par(min(i, self.n - 2))
            if i == self.n - 1 and not self.superponer.isChecked():
                self.deslizador.setValue(PASOS)  # la última solo puede ser la segunda del último par
        self.visor.setFocus()

    def _abrir_selector(self) -> None:
        if self.selector.isEnabled():
            self.selector.setFocus()
            self.selector.showPopup()

    def _anterior(self) -> None:
        if self.modo_video:
            i, _ = self.ritmo.estado(self._t, self.n) if self.n else (0, 0)
            self._ir_a(self.ritmo.inicio(max(0, i - 1)))
        else:
            self._ir_a_par(self._par - 1)

    def _siguiente(self) -> None:
        if self.modo_video:
            i, _ = self.ritmo.estado(self._t, self.n) if self.n else (0, 0)
            self._ir_a(self.ritmo.inicio(min(self.n - 1, i + 1)))
        else:
            self._ir_a_par(self._par + 1)

    def _superponer_cambiado(self) -> None:
        if self.superponer.isChecked():
            self.deslizador.blockSignals(True)
            self.deslizador.setValue(PASOS // 2)  # el deslizador refleja la mezcla al 50 %
            self.deslizador.blockSignals(False)
        self._actualizar_controles()
        self._refrescar()

    # ---------------------------------------------------------------- modo vídeo

    def _cambiar_modo(self) -> None:
        self._pausar()
        if self.modo_video:
            self._t = self.ritmo.inicio(self._par)
            self._ajustar_linea()
            self.paneles.setCurrentIndex(1)
        else:
            if self.n >= 2:
                i, _ = self.ritmo.estado(self._t, self.n)
                self._par = min(i, self.n - 2)
                self._reiniciar_deslizador()
            self.paneles.setCurrentIndex(0)
        self._actualizar_controles()
        self._refrescar()

    def _ajustar_linea(self) -> None:
        self.linea.blockSignals(True)
        self.linea.setRange(0, max(1, round(self.ritmo.duracion(self.n) * 1000)))
        self.linea.setPageStep(max(1, round(self.ritmo.paso * 1000)))
        self.linea.blockSignals(False)

    def _ritmo_cambiado(self) -> None:
        i, _ = self.ritmo.estado(self._t, self.n) if self.n else (0, 0.0)
        self.ritmo = Ritmo(self.spn_foto.value(), self.spn_transicion.value(), bool(self.cmb_transicion.currentData()))
        if self.ritmo.duracion(self.n) <= 0:
            self._pausar()
        self._t = self.ritmo.inicio(i)  # se conserva la foto que se estaba viendo
        self._ajustar_linea()
        self._actualizar_controles()
        self.config.guardar_ritmo(self.ritmo)
        if self.modo_video:
            self._refrescar()

    def _poner_ritmo(self, ritmo: Ritmo) -> None:
        """Lleva un ritmo a los controles del modo vídeo (y, por _ritmo_cambiado, lo aplica y guarda)."""
        for w in (self.cmb_transicion, self.spn_foto, self.spn_transicion):
            w.blockSignals(True)
        self.cmb_transicion.setCurrentIndex(self.cmb_transicion.findData(ritmo.fundido))
        self.spn_foto.setValue(ritmo.t_foto)
        self.spn_transicion.setValue(ritmo.t_transicion)
        for w in (self.cmb_transicion, self.spn_foto, self.spn_transicion):
            w.blockSignals(False)
        self._ritmo_cambiado()

    def _linea_movida(self, valor: int) -> None:
        self._t = valor / 1000.0
        self._refrescar()

    def _ir_a(self, t: float) -> None:
        if not self.modo_video or self.n < 2:
            return
        self._t = min(max(0.0, t), self.ritmo.duracion(self.n))
        self.cronometro.restart()
        self._refrescar()

    def _ir_al_final(self) -> None:
        self._pausar()
        self._ir_a(self.ritmo.duracion(self.n))

    def _alternar_reproduccion(self) -> None:
        if not self.modo_video or self.n < 2:
            return
        if self.reloj.isActive():
            self._pausar()
            return
        dur = self.ritmo.duracion(self.n)
        if dur <= 0:
            return
        if self._t >= dur - 1e-6:
            self._t = 0.0  # al final, reproducir vuelve a empezar
        self.cronometro.start()
        self.reloj.start()
        self.btn_reproducir.setText("Pausar")
        self.btn_reproducir.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self._refrescar()

    def _pausar(self) -> None:
        if self.reloj.isActive() and self.visor.zoom > 1.0:
            self._espera_alta.start()
        self.reloj.stop()
        self.btn_reproducir.setText("Reproducir")
        self.btn_reproducir.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

    def _tic(self) -> None:
        # si cargar una foto tarda, no se salta contenido: el avance por tic está acotado
        dt = min(self.cronometro.restart() / 1000.0, 0.25)
        dur = self.ritmo.duracion(self.n)
        self._t = min(self._t + dt, dur)
        if self._t >= dur:
            self._pausar()
        self._refrescar()

    # ---------------------------------------------------------------- abrir y analizar

    def _carpeta_inicial(self) -> str:
        if self.proyecto:
            return str(self.proyecto.rutas[0].parent)
        guardada = self.config.texto("archivos/carpeta")
        return guardada if guardada and Path(guardada).is_dir() else str(Path.home())

    def _abrir_fotos(self) -> None:
        filtro = "Imágenes (" + " ".join(f"*{e}" for e in sorted(EXTENSIONES)) + ");;Todos los ficheros (*)"
        ficheros, _ = QFileDialog.getOpenFileNames(self, "Abrir fotografías", self._carpeta_inicial(), filtro)
        if ficheros:
            # el diálogo no garantiza ningún orden: se aplica el orden por nombre (secuencia o fecha)
            self._abrir_argumentos([str(r) for r in ordenar_por_nombre([Path(f) for f in ficheros])])

    def _abrir_carpeta(self) -> None:
        carpeta = QFileDialog.getExistingDirectory(self, "Abrir carpeta de fotografías", self._carpeta_inicial())
        if carpeta:
            self._abrir_argumentos([carpeta])

    def _abrir_argumentos(self, argumentos: list[str]) -> None:
        try:
            rutas = recopilar(argumentos, self.orden)
        except ErrorEntrada as e:
            QMessageBox.warning(self, "Evoluciona", str(e))
            return
        self.abrir(rutas)

    def _cambiar_alineacion(self) -> None:
        if not self.proyecto:
            return
        d = DialogoAlineacion(self.opciones, self)
        if d.exec() == QDialog.Accepted and d.opciones() != self.opciones:
            self.abrir(self.proyecto.rutas, d.opciones())

    def abrir(self, rutas: list[Path], opciones: Opciones | None = None) -> None:
        """Analiza las fotos en segundo plano y, al terminar, las muestra."""
        if self._hilo is not None or not rutas:
            return
        self._pausar()
        opciones = opciones or self.opciones
        d = QProgressDialog("Preparando…", "Cancelar", 0, len(rutas), self)
        d.setWindowTitle("Alineando fotografías")
        d.setWindowModality(Qt.WindowModal)
        d.setMinimumDuration(300)
        d.setAutoClose(False)
        d.setAutoReset(False)
        d.setMinimumWidth(420)
        hilo = HiloAnalisis(list(rutas), opciones, self)
        hilo.progreso.connect(self._progreso_analisis)
        hilo.listo.connect(lambda p: self._analisis_listo(p, opciones))
        hilo.fallo.connect(self._tarea_fallida)
        d.canceled.connect(hilo.requestInterruption)
        self._hilo, self._dialogo_progreso = hilo, d
        hilo.start()

    def _progreso_analisis(self, i: int, n: int, texto: str) -> None:
        d = self._dialogo_progreso
        if d is not None and not d.wasCanceled():
            d.setMaximum(n)
            d.setValue(i)
            d.setLabelText(texto)

    def _fin_tarea(self) -> None:
        if self._hilo is not None:
            self._hilo.wait()
            self._hilo.deleteLater()
            self._hilo = None
        if self._dialogo_progreso is not None:
            self._dialogo_progreso.blockSignals(True)
            self._dialogo_progreso.close()
            self._dialogo_progreso.deleteLater()
            self._dialogo_progreso = None

    def _tarea_fallida(self, mensaje: str) -> None:
        self._fin_tarea()
        if mensaje:  # vacío = cancelado por el usuario
            QMessageBox.critical(self, "Evoluciona", mensaje)

    def _analisis_listo(self, proyecto: Proyecto, opciones: Opciones) -> None:
        self._fin_tarea()
        if self.cache is not None:
            self.cache.cerrar()
        self.opciones = opciones
        self.config.guardar_opciones(opciones)
        self.config.guardar("archivos/carpeta", str(proyecto.rutas[0].parent))
        self.proyecto = proyecto
        self.cache = CacheFotogramas(proyecto, proyecto.tam_salida(lado_max=LADO_PREVIA))
        self._qimagenes.clear()
        self._contornos = {}
        self._generacion += 1
        self._alta.clear()
        self._pedidas.clear()
        self.visor.vaciar()
        self._par, self._t = 0, 0.0
        if self.n < 2 and self.modo_video:
            self.act_video.setChecked(False)
            self.paneles.setCurrentIndex(0)
        self._reiniciar_deslizador()
        self.selector.clear()
        self.selector.addItems([f"{k + 1} · {r.name}" for k, r in enumerate(proyecto.rutas)])
        self._ajustar_linea()
        self._actualizar_controles()
        self.setWindowTitle(f"Evoluciona — {proyecto.rutas[0].parent.name} ({self.n} fotos)")
        self._refrescar()
        if proyecto.avisos:
            QMessageBox.information(self, "Evoluciona", "\n\n".join(proyecto.avisos))

    # ---------------------------------------------------------------- exportar

    def _exportar(self) -> None:
        if not self.proyecto or self._hilo is not None:
            return
        self._pausar()
        if not ffmpeg_disponible():
            QMessageBox.critical(self, "Generar vídeo",
                                 "No se encuentra ffmpeg. Instálalo con:\n\nsudo apt install ffmpeg")
            return
        d = DialogoExportar(self.proyecto, self.ritmo, self.proyecto.rutas[0].parent / "evoluciona.mp4", self,
                            self.config.video())
        if d.exec() != QDialog.Accepted:
            return
        ajustes, salida = d.ajustes(), d.salida()
        self.config.guardar_video(ajustes.formato, ajustes.alto, ajustes.fps, ajustes.calidad)
        if ajustes.ritmo != self.ritmo:
            self._poner_ritmo(ajustes.ritmo)  # el modo vídeo usa el mismo ritmo que el vídeo generado
        prog = QProgressDialog("Generando vídeo…", "Cancelar", 0, 100, self)
        prog.setWindowTitle("Generar vídeo")
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        prog.setMinimumWidth(420)
        hilo = HiloExportacion(self.proyecto, salida, ajustes, self)
        hilo.progreso.connect(self._progreso_exportacion)
        hilo.listo.connect(self._exportacion_lista)
        hilo.fallo.connect(self._tarea_fallida)
        prog.canceled.connect(hilo.requestInterruption)
        self._hilo, self._dialogo_progreso = hilo, prog
        hilo.start()
        prog.setValue(0)

    def _progreso_exportacion(self, k: int, total: int) -> None:
        d = self._dialogo_progreso
        if d is not None and not d.wasCanceled():
            d.setMaximum(total)
            d.setValue(k)
            d.setLabelText(f"Generando vídeo… fotograma {k} de {total}")

    def _exportacion_lista(self, ruta: str) -> None:
        self._fin_tarea()
        caja = QMessageBox(QMessageBox.Information, "Generar vídeo", f"Vídeo guardado en:\n{ruta}",
                           QMessageBox.Ok, self)
        abrir = caja.addButton("Abrir vídeo", QMessageBox.ActionRole)
        caja.exec()
        if caja.clickedButton() is abrir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(ruta))

    # ---------------------------------------------------------------- zoom y ayuda

    def _zoom_cambiado(self, zoom: float) -> None:
        self.lbl_zoom.setText("Zoom: ajustado" if zoom <= 1.0 else f"Zoom: {round(zoom * 100)} %")
        self.lbl_zoom.setToolTip("Porcentaje respecto al tamaño ajustado a la ventana")
        if zoom > 1.0 and not self.reloj.isActive():
            self._espera_alta.start()

    def _pedir_alta(self) -> None:
        """Si el zoom supera la resolución de la previsualización, genera las fotos visibles a tamaño completo."""
        if not self.proyecto or self.cache is None or self.reloj.isActive():
            return
        completo = self.proyecto.tam_salida()
        if completo[0] <= self.cache.tam[0] or self.visor.ancho_necesario() <= self.cache.tam[0] * 1.15:
            return
        for i in sorted(self.visor.indices()):
            if i not in self._alta and i not in self._pedidas:
                self._pedidas.add(i)
                self._ejecutor_alta.submit(self._calcular_alta, self.proyecto, self._generacion, i, completo)

    def _calcular_alta(self, proyecto: Proyecto, generacion: int, i: int, tam: tuple[int, int]) -> None:
        try:  # se ejecuta en otro hilo
            arr = proyecto.fotograma(i, tam)
        except Exception:
            arr = None  # se sigue usando la previsualización
        try:
            self._puente.listo.emit(generacion, i, arr)
        except RuntimeError:
            pass  # la ventana ya se ha cerrado

    def _alta_lista(self, generacion: int, i: int, arr) -> None:
        if generacion != self._generacion:
            return
        self._pedidas.discard(i)
        if arr is None:
            return
        alto, ancho = arr.shape[:2]
        visibles = self.visor.indices()
        for k in [k for k in self._alta if k not in visibles][: max(0, len(self._alta) + 1 - MAX_ALTA)]:
            del self._alta[k]
        self._alta[i] = QImage(arr.data, ancho, alto, 3 * ancho, QImage.Format_RGB888).copy()
        if i in visibles:
            self._refrescar()

    def _mostrar_ayuda(self) -> None:
        caja = QMessageBox(QMessageBox.NoIcon, "Atajos de teclado y ratón", AYUDA, QMessageBox.Close, self)
        caja.setTextFormat(Qt.RichText)
        caja.exec()

    def _acerca_de(self) -> None:
        QMessageBox.about(self, "Acerca de Evoluciona",
                          f"<b>Evoluciona {version_completa()}</b><p>Alinea las fotografías de un timelapse sobre "
                          "la figura en primer plano, las compara por pares y genera vídeo.</p>"
                          f"<p>Detección de la figura: {self._dispositivo()}</p>")

    def _dispositivo(self) -> str:
        from . import segmentacion

        try:
            ort = segmentacion._importar_ort()
        except RuntimeError as e:
            return str(e)
        if not segmentacion.gpu_preferida():
            return "CPU (forzada)"
        if segmentacion.cuda_disponible(ort):
            return "GPU (CUDA) si funciona; se comprueba al analizar (si falla, CPU con aviso)"
        return "CPU"

    # ---------------------------------------------------------------- cierre

    def closeEvent(self, evento) -> None:
        self._pausar()
        if self._hilo is not None:
            self._hilo.requestInterruption()
            self._hilo.wait()
        if self.cache is not None:
            self.cache.cerrar()
        self._ejecutor_alta.shutdown(wait=False, cancel_futures=True)
        self.config.guardar("ventana/geometria", self.saveGeometry())
        self.config.sincronizar()
        super().closeEvent(evento)


def _en_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version", encoding="utf-8", errors="replace") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def ejecutar(rutas: list[Path], opciones_cli: dict | None = None, ritmo_cli: dict | None = None,
             orden: str = "nombre") -> int:
    """Abre la ventana. ``opciones_cli`` y ``ritmo_cli``: lo indicado en la línea de órdenes, que prevalece
    sobre la configuración guardada."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl+C en la terminal cierra la aplicación
    if _en_wsl() and "QT_QPA_PLATFORM" not in os.environ and os.environ.get("DISPLAY"):
        # En WSLg, con Wayland los desplegables de Qt se quedan abiertos tras elegir un valor;
        # con X11 (XWayland) funcionan bien. QT_QPA_PLATFORM=wayland lo evita si se prefiere.
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Evoluciona")
    app.setDesktopFileName("evoluciona")  # app_id de Wayland: enlaza la ventana con evoluciona.desktop
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("recursos") / "evoluciona.svg")))
    traductor = QTranslator(app)  # botones estándar de Qt (Aceptar, Cancelar, Sí, No…) en español
    if traductor.load("qtbase_es", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(traductor)
    config = Configuracion()
    opciones = combinar(config.opciones(), opciones_cli or {})
    ritmo = combinar(config.ritmo(), ritmo_cli or {})
    ventana = Ventana(opciones, ritmo, orden, config)
    geometria = config.geometria()
    if geometria is not None:
        ventana.restoreGeometry(geometria)
    ventana.show()
    if rutas:
        QTimer.singleShot(0, lambda: ventana.abrir(rutas))
    return app.exec()
