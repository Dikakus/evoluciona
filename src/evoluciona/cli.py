"""Punto de entrada: abre la ventana o exporta el vídeo directamente."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import version_completa
from .alineacion import METODOS
from .entrada import ErrorEntrada, recopilar
from .linea_tiempo import Ritmo
from .segmentacion import MODELOS


def _analizador() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="evoluciona",
        description="Alinea las fotografías de un timelapse sobre la figura en primer plano, "
                    "permite compararlas por pares y genera un vídeo.",
    )
    p.add_argument("fotos", nargs="*", metavar="FOTO",
                   help="ficheros, directorios o patrones (p. ej. 'fotos/*.jpg'); si se omite, se eligen en la ventana")
    p.add_argument("--orden", choices=("nombre", "fecha", "dado"), default="nombre",
                   help="orden de las fotos: nombre (por defecto; sigue la secuencia numérica o de fechas del "
                        "nombre: foto-2 antes que foto-10), fecha (de captura) o dado (tal como se indican)")
    p.add_argument("--cpu", action="store_true",
                   help="no usar la GPU aunque esté disponible (equivale a EVOLUCIONA_DISPOSITIVO=cpu)")
    p.add_argument("--diagnostico", action="store_true",
                   help="muestra la versión y si la detección de la figura puede usar la GPU, y termina")
    p.add_argument("--version", action="version", version=f"%(prog)s {version_completa()}")

    a = p.add_argument_group("alineación")
    a.add_argument("--metodo", choices=METODOS, default=None,
                   help="contorno: máximo solape de siluetas (por defecto); altura: iguala la altura de la figura; "
                        "area: iguala su superficie; ninguno: sin alinear")
    a.add_argument("--referencia", choices=("anterior", "primera"), default=None,
                   help="cada foto se alinea con la anterior (por defecto) o siempre con la primera")
    a.add_argument("--modelo", choices=tuple(MODELOS), default=None,
                   help="modelo de segmentación: general (cualquier figura), persona o rapido")
    a.add_argument("--sin-recorte", action="store_true", default=None,
                   help="no recortar a la zona común (se verán bordes negros)")

    v = p.add_argument_group("vídeo")
    v.add_argument("--exportar", metavar="SALIDA", help="genera el vídeo en SALIDA sin abrir la ventana")
    v.add_argument("--formato", choices=("mp4", "mp4-h265", "webm", "gif"), default=None,
                   help="formato del vídeo (por defecto, según la extensión de SALIDA)")
    v.add_argument("--transicion", choices=("fundido", "corte"), default=None,
                   help="fundido entre fotos (por defecto) o cambio sin fundido")
    v.add_argument("--t-transicion", type=float, default=None, metavar="S", help="duración del fundido en segundos (1; en la ventana, la guardada)")
    v.add_argument("--t-foto", type=float, default=None, metavar="S", help="segundos que se muestra cada foto (1; en la ventana, la guardada)")
    v.add_argument("--fps", type=float, default=30.0, help="fotogramas por segundo (30)")
    v.add_argument("--alto", type=int, default=None, metavar="PX", help="altura del vídeo en píxeles (por defecto, la original)")
    v.add_argument("--calidad", choices=("alta", "media", "baja"), default="alta")
    return p


def _exportar(args, rutas, opciones, ritmo) -> int:
    from .proyecto import analizar
    from .video import AjustesVideo, ErrorVideo, exportar

    salida = Path(args.exportar).expanduser()
    formato = args.formato or {".webm": "webm", ".gif": "gif"}.get(salida.suffix.lower(), "mp4")
    ajustes = AjustesVideo(formato=formato, fps=args.fps, alto=args.alto, calidad=args.calidad, ritmo=ritmo)

    def barra(etiqueta):
        def f(i, n, texto=""):
            print(f"\r{etiqueta}: {i}/{n} {texto[:50]:<50}", end="", file=sys.stderr, flush=True)
        return f

    try:
        proyecto = analizar(rutas, opciones, progreso=barra("Alineando"))
        print(file=sys.stderr)
        for aviso in proyecto.avisos:
            print(f"Aviso: {aviso}", file=sys.stderr)
        escrito = exportar(proyecto, salida, ajustes, progreso=barra("Fotogramas"))
        print(file=sys.stderr)
    except (ErrorVideo, ValueError, RuntimeError, OSError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    print(escrito)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = _analizador()
    args = p.parse_args(argv)
    if (args.t_foto or 0) < 0 or (args.t_transicion or 0) < 0:
        p.error("los tiempos no pueden ser negativos")
    if args.alto is not None and args.alto < 16:
        p.error("--alto debe ser al menos 16")

    from . import segmentacion

    if args.cpu:
        segmentacion.USAR_GPU = False
    if args.diagnostico:
        print(f"Evoluciona {version_completa()}")
        print(f"Python {sys.version.split()[0]} · {sys.executable}")
        try:
            lineas = segmentacion.diagnostico()
        except Exception as e:
            print(f"Error en el diagnóstico: {e}", file=sys.stderr)
            return 1
        print("\n".join(lineas))
        return 0

    from .proyecto import Opciones

    try:
        rutas = recopilar(args.fotos, args.orden) if args.fotos else []
    except ErrorEntrada as e:
        p.error(str(e))
    # solo lo indicado expresamente: en la ventana se combina con la configuración guardada
    opc = {k: v for k, v in (("metodo", args.metodo), ("referencia", args.referencia), ("modelo", args.modelo),
                             ("recortar", False if args.sin_recorte else None)) if v is not None}
    rit = {k: v for k, v in (("t_foto", args.t_foto), ("t_transicion", args.t_transicion),
                             ("fundido", None if args.transicion is None else args.transicion == "fundido"))
           if v is not None}

    if args.exportar:  # sin ventana no se usa la configuración guardada: valores por defecto + opciones
        if not rutas:
            p.error("--exportar necesita al menos una fotografía")
        return _exportar(args, rutas, Opciones(**opc), Ritmo(**rit))

    from .gui import ejecutar

    return ejecutar(rutas, opc, rit, orden=args.orden)


if __name__ == "__main__":
    raise SystemExit(main())
