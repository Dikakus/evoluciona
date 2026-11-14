"""Recopila las fotografías de entrada: ficheros sueltos, directorios o globs."""

from __future__ import annotations

import glob
import os
import re
from datetime import date, datetime
from pathlib import Path

EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
ORDENES = ("nombre", "fecha", "dado")


class ErrorEntrada(Exception):
    pass


def _tokens(texto: str) -> list[tuple[int, object]]:
    """Trozos numéricos y de letras; los separadores (- _ . espacio…) no cuentan: foto-2 = foto_2."""
    return [(0, int(p)) if p.isdigit() else (1, p) for p in re.findall(r"\d+|[^\W\d_]+", texto.lower())]


def _clave_natural(ruta: Path):
    """Orden natural: 'foto2' va antes que 'foto10'."""
    return _tokens(ruta.name), str(ruta)


def es_imagen(ruta: Path) -> bool:
    return ruta.suffix.lower() in EXTENSIONES


def _fecha(ruta: Path) -> float:
    """Fecha de captura (EXIF) o, si no hay, de modificación del fichero."""
    try:
        from PIL import Image

        with Image.open(ruta) as im:
            exif = im.getexif()
            # 36867 = DateTimeOriginal (sub-IFD Exif), 306 = DateTime
            texto = exif.get_ifd(0x8769).get(36867) or exif.get(306)
        if texto:
            return datetime.strptime(str(texto).strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        pass
    try:
        return ruta.stat().st_mtime
    except OSError:
        return 0.0


_ANO = r"(?:199\d|20\d\d)"  # años de fotos digitales: «193315» no es 1933 sino, p. ej., una hora
_PATRONES = (
    re.compile(rf"(?<!\d)(?P<a>{_ANO})(?P<sep>[-_. ])(?P<m>\d{{1,2}})(?P=sep)(?P<d>\d{{1,2}})(?!\d)"),  # 2026-10-11
    re.compile(rf"(?<!\d)(?P<d>\d{{1,2}})(?P<sep>[-_. ])(?P<m>\d{{1,2}})(?P=sep)(?P<a>{_ANO})(?!\d)"),  # 11-10-2026
    re.compile(rf"(?<!\d)(?P<a>{_ANO})(?P<md>\d{{4}})(?=\d{{6}}(?!\d))"),  # 20261011153000 (fecha y hora)
    re.compile(rf"(?<!\d)(?P<a>{_ANO})(?P<md>\d{{2,4}})(?!\d)"),  # 20261011, 2026111, 202615
)


def _valida(a: int, m: int, d: int) -> date | None:
    try:
        return date(a, m, d)
    except ValueError:
        return None


def fechas_en_nombre(nombre: str) -> tuple[int, int, list[date]] | None:
    """Busca una fecha en el nombre: devuelve (inicio, fin, interpretaciones posibles).

    Admite fechas con separadores (año-mes-día o día-mes-año) y compactas, también sin
    ceros a la izquierda. Estas últimas pueden ser ambiguas: «2026111» es tanto el 11 de
    enero como el 1 de noviembre, y entonces se devuelven las dos interpretaciones.
    """
    for patron in _PATRONES:
        for c in patron.finditer(nombre):
            a = int(c["a"])
            grupos = c.groupdict()
            if "md" not in grupos:
                pares = [(int(c["m"]), int(c["d"]))]
            else:
                md = c["md"]
                if len(md) == 4:
                    pares = [(int(md[:2]), int(md[2:]))]
                elif len(md) == 2:
                    pares = [(int(md[0]), int(md[1]))]
                else:  # tres cifras: mes de una cifra + día de dos, o mes de dos + día de una
                    pares = [(int(md[:2]), int(md[2]))]
                    if md[1] != "0":  # nadie escribe el día con cero a la izquierda y el mes sin él
                        pares.append((int(md[0]), int(md[1:])))
            fechas = [f for m, d in pares if (f := _valida(a, m, d))]
            if fechas:
                return c.start(), c.end(), fechas
    return None


def _resto(texto: str) -> list[tuple[int, object]]:
    """Lo que sigue a la fecha, para desempatar: sus cifras juntas (la hora 22-40-35 = 224035) y sus letras."""
    cifras = "".join(re.findall(r"\d", texto))
    return ([(0, int(cifras))] if cifras else []) + [(1, p) for p in re.findall(r"[^\W\d_]+", texto.lower())]


def ordenar_por_nombre(rutas: list[Path]) -> list[Path]:
    """Ordena por la secuencia del nombre: numérica (foto-2 antes que foto-10) o de fechas.

    Si al menos la mitad de los nombres contienen una fecha, se ordena cronológicamente por ella,
    aunque esté escrita sin ceros (nombre-20261011, nombre-2026111) o como día-mes-año; lo que
    va detrás (p. ej. la hora: _124118) desempata. Los nombres sin fecha se colocan por su fecha
    y hora de captura (EXIF o, si no hay, de modificación). Cuando una fecha compacta es ambigua
    se elige la interpretación más cercana a la fecha de captura. Si la mayoría de los nombres no
    tienen fecha, se usa el orden natural. Los separadores (- _ . espacio) no distinguen series.
    """
    halladas = [fechas_en_nombre(r.stem) for r in rutas]
    if not rutas or 2 * sum(h is not None for h in halladas) < len(rutas):
        return sorted(rutas, key=_clave_natural)

    def clave(par):
        ruta, hallada = par
        nombre = ruta.stem
        if hallada is None:  # sin fecha en el nombre: la de captura, con su hora para desempatar
            momento = datetime.fromtimestamp(_fecha(ruta))
            prefijo = re.split(r"\d", nombre, maxsplit=1)[0]
            resto = [(0, int(momento.strftime("%H%M%S")))]
            return (_tokens(prefijo), momento.date().toordinal(), resto, str(ruta))
        ini, fin, fechas = hallada
        f = fechas[0]
        if len(fechas) > 1:
            real = date.fromtimestamp(_fecha(ruta))
            f = min(fechas, key=lambda x: abs((x - real).days))
        return (_tokens(nombre[:ini]), f.toordinal(), _resto(nombre[fin:]), str(ruta))

    return [r for r, _ in sorted(zip(rutas, halladas), key=clave)]


def recopilar(argumentos: list[str], orden: str = "nombre") -> list[Path]:
    """Devuelve la lista ordenada de fotografías.

    Cada argumento puede ser un fichero, un directorio (se toman sus imágenes, sin
    recursión) o un glob (admite ``**``). ``orden``: ``"nombre"`` (secuencia numérica o de
    fechas del nombre, ver :func:`ordenar_por_nombre`), ``"fecha"`` (fecha de captura) o
    ``"dado"`` (tal como se indican; dentro de cada directorio o glob, por nombre).
    """
    if orden not in ORDENES:
        raise ErrorEntrada(f"Orden desconocido: {orden}")
    rutas: list[Path] = []
    for arg in argumentos:
        p = Path(arg).expanduser()
        if p.is_dir():
            encontrados = ordenar_por_nombre([f for f in p.iterdir() if f.is_file() and es_imagen(f)])
            if not encontrados:
                raise ErrorEntrada(f"El directorio no contiene imágenes: {arg}")
            rutas.extend(encontrados)
        elif p.is_file():
            rutas.append(p)
        elif glob.has_magic(arg):
            coincidencias = [Path(c) for c in glob.glob(os.path.expanduser(arg), recursive=True)]
            encontrados = ordenar_por_nombre([f for f in coincidencias if f.is_file() and es_imagen(f)])
            if not encontrados:
                raise ErrorEntrada(f"El patrón no coincide con ninguna imagen: {arg}")
            rutas.extend(encontrados)
        else:
            raise ErrorEntrada(f"No existe: {arg}")

    vistos: set[Path] = set()
    unicas: list[Path] = []
    for r in rutas:
        clave = r.resolve()
        if clave not in vistos:
            vistos.add(clave)
            unicas.append(r)

    if orden == "fecha":
        unicas.sort(key=lambda r: (_fecha(r), _clave_natural(r)))
    elif orden == "nombre":
        unicas = ordenar_por_nombre(unicas)
    return unicas
