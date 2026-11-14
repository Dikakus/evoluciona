"""Exportación de la secuencia a vídeo mediante ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2

from .linea_tiempo import Ritmo
from .proyecto import Cancelado, Proyecto


@dataclass(frozen=True)
class Formato:
    nombre: str
    extension: str
    args: tuple[str, ...]  # argumentos de códec; {crf} se sustituye según la calidad
    crf: tuple[int, int, int]  # alta, media, baja


FORMATOS: dict[str, Formato] = {
    "mp4": Formato("MP4 (H.264) — máxima compatibilidad", ".mp4",
                   ("-c:v", "libx264", "-preset", "medium", "-crf", "{crf}", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart"), (17, 21, 26)),
    "mp4-h265": Formato("MP4 (H.265/HEVC) — más compacto", ".mp4",
                        ("-c:v", "libx265", "-preset", "medium", "-crf", "{crf}", "-pix_fmt", "yuv420p",
                         "-tag:v", "hvc1", "-movflags", "+faststart"), (20, 24, 28)),
    "webm": Formato("WebM (VP9)", ".webm",
                    ("-c:v", "libvpx-vp9", "-crf", "{crf}", "-b:v", "0", "-row-mt", "1",
                     "-pix_fmt", "yuv420p"), (24, 31, 38)),
    "gif": Formato("GIF animado", ".gif",
                   ("-filter_complex", "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a",
                    "-loop", "0"), (0, 0, 0)),
}
CALIDADES = ("alta", "media", "baja")


@dataclass(frozen=True)
class AjustesVideo:
    formato: str = "mp4"
    fps: float = 30.0
    alto: int | None = None  # None = altura original del lienzo
    calidad: str = "alta"
    ritmo: Ritmo = Ritmo()


class ErrorVideo(Exception):
    pass


def ffmpeg_disponible() -> bool:
    return shutil.which("ffmpeg") is not None


def exportar(
    proyecto: Proyecto,
    salida: Path,
    ajustes: AjustesVideo = AjustesVideo(),
    progreso: Callable[[int, int], None] | None = None,
    cancelado: Callable[[], bool] | None = None,
) -> Path:
    """Genera el vídeo. Devuelve la ruta escrita."""
    if not ffmpeg_disponible():
        raise ErrorVideo("No se encuentra ffmpeg en el sistema (instálalo con: sudo apt install ffmpeg)")
    if ajustes.formato not in FORMATOS:
        raise ErrorVideo(f"Formato desconocido: {ajustes.formato}")
    if ajustes.calidad not in CALIDADES:
        raise ErrorVideo(f"Calidad desconocida: {ajustes.calidad}")
    if not 1 <= ajustes.fps <= 120:
        raise ErrorVideo("Los fotogramas por segundo deben estar entre 1 y 120")
    n = len(proyecto)
    ritmo = ajustes.ritmo
    total = max(1, round(ritmo.duracion(n) * ajustes.fps))
    if ritmo.duracion(n) <= 0:
        raise ErrorVideo("La duración del vídeo es cero: aumenta el tiempo por foto o el de transición")

    fmt = FORMATOS[ajustes.formato]
    salida = Path(salida)
    if salida.suffix.lower() != fmt.extension:
        salida = salida.with_suffix(fmt.extension)
    if not salida.parent.is_dir():
        raise ErrorVideo(f"No existe la carpeta de destino: {salida.parent}")
    tam = proyecto.tam_salida(alto=ajustes.alto, par=True)
    crf = str(fmt.crf[CALIDADES.index(ajustes.calidad)])
    # se escribe a un temporal en el mismo directorio para no dejar un vídeo a medias
    temporal = salida.with_name(f".{salida.stem}.parcial{salida.suffix}")
    orden = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{tam[0]}x{tam[1]}", "-r", f"{ajustes.fps:g}", "-i", "-",
        "-an", *[a.replace("{crf}", crf) for a in fmt.args], str(temporal),
    ]

    with tempfile.TemporaryFile() as errores:
        proc = subprocess.Popen(orden, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errores)
        try:
            cargados: dict[int, object] = {}  # como mucho dos fotos en memoria

            def foto(i: int):
                if i not in cargados:
                    for k in [k for k in cargados if k < i - 1]:
                        del cargados[k]
                    cargados[i] = proyecto.fotograma(i, tam)
                return cargados[i]

            previo: tuple[int, float] | None = None
            datos = b""
            for k in range(total):
                if cancelado and cancelado():
                    raise Cancelado()
                i, alfa = ritmo.estado((k + 0.5) / ajustes.fps, n)
                if (i, alfa) != previo:
                    if alfa <= 0.0:
                        datos = foto(i).tobytes()
                    else:
                        datos = cv2.addWeighted(foto(i), 1.0 - alfa, foto(i + 1), alfa, 0.0).tobytes()
                    previo = (i, alfa)
                try:
                    proc.stdin.write(datos)
                except (BrokenPipeError, OSError):
                    break  # ffmpeg ha terminado antes de tiempo; el error se informa abajo
                if progreso:
                    progreso(k + 1, total)
            try:
                proc.stdin.close()
            except OSError:
                pass
            codigo = proc.wait()
            if codigo != 0:
                errores.seek(0)
                detalle = errores.read().decode(errors="replace").strip()[-800:]
                raise ErrorVideo(f"ffmpeg ha fallado (código {codigo}):\n{detalle}")
            temporal.replace(salida)
            return salida
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            temporal.unlink(missing_ok=True)
