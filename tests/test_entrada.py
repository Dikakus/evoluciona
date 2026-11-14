import pytest
from PIL import Image

from pathlib import Path

from evoluciona.entrada import ErrorEntrada, fechas_en_nombre, ordenar_por_nombre, recopilar


@pytest.fixture
def carpeta(tmp_path):
    for nombre in ("foto10.jpg", "foto2.jpg", "foto1.JPG", "notas.txt", "otra.png"):
        if nombre.endswith(".txt"):
            (tmp_path / nombre).write_text("x")
        else:
            Image.new("RGB", (8, 8)).save(tmp_path / nombre)
    return tmp_path


def nombres(rutas):
    return [r.name for r in rutas]


def test_directorio_orden_natural(carpeta):
    assert nombres(recopilar([str(carpeta)])) == ["foto1.JPG", "foto2.jpg", "foto10.jpg", "otra.png"]


def test_glob(carpeta):
    assert nombres(recopilar([str(carpeta / "foto*.jpg")])) == ["foto2.jpg", "foto10.jpg"]


def test_ficheros_sin_duplicados(carpeta):
    args = [str(carpeta / "otra.png"), str(carpeta / "foto2.jpg"), str(carpeta / "otra.png")]
    assert nombres(recopilar(args)) == ["foto2.jpg", "otra.png"]  # por defecto, por nombre
    assert nombres(recopilar(args, orden="dado")) == ["otra.png", "foto2.jpg"]


def test_errores(carpeta, tmp_path):
    with pytest.raises(ErrorEntrada):
        recopilar([str(carpeta / "no_existe.jpg")])
    with pytest.raises(ErrorEntrada):
        recopilar([str(carpeta / "*.gif")])
    vacio = tmp_path / "vacio"
    vacio.mkdir()
    with pytest.raises(ErrorEntrada):
        recopilar([str(vacio)])


def orden(*nombres_):
    return [r.name for r in ordenar_por_nombre([Path(n) for n in nombres_])]


def test_secuencia_numerica():
    assert orden("nombre-10.jpg", "nombre-2.jpg", "nombre-1.jpg", "nombre-3.jpg") == \
        ["nombre-1.jpg", "nombre-2.jpg", "nombre-3.jpg", "nombre-10.jpg"]


def test_secuencia_de_fechas_sin_ceros():
    # 202612 = 2 de enero; 2026915 = 15 de septiembre; 20261011 = 11 de octubre
    assert orden("nombre-20261011.jpg", "nombre-2026915.jpg", "nombre-202612.jpg", "nombre-20260105.jpg") == \
        ["nombre-202612.jpg", "nombre-20260105.jpg", "nombre-2026915.jpg", "nombre-20261011.jpg"]


def test_fechas_dia_mes_ano():
    assert orden("f 11-10-2026.jpg", "f 2-9-2026.jpg", "f 1-11-2026.jpg") == \
        ["f 2-9-2026.jpg", "f 11-10-2026.jpg", "f 1-11-2026.jpg"]


def test_fecha_ambigua():
    ini, fin, fechas = fechas_en_nombre("nombre-2026111")
    assert (ini, fin) == (7, 14) and len(fechas) == 2


def test_sin_fecha_en_todos_usa_orden_natural():
    assert orden("b-20261011.jpg", "a-3.jpg", "a-20.jpg") == ["a-3.jpg", "a-20.jpg", "b-20261011.jpg"]
