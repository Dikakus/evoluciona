"""Configuración guardada entre sesiones (QSettings en un fichero temporal; no necesita pantalla)."""

from PySide6.QtCore import QSettings

from evoluciona.configuracion import Configuracion, combinar
from evoluciona.linea_tiempo import Ritmo
from evoluciona.proyecto import Opciones


def config(tmp_path):
    return Configuracion(QSettings(str(tmp_path / "evoluciona.conf"), QSettings.IniFormat))


def test_por_defecto(tmp_path):
    c = config(tmp_path)
    assert c.opciones() == Opciones() and c.ritmo() == Ritmo()
    assert c.video() == {"formato": "mp4", "alto": 1080, "fps": 30.0, "calidad": "alta"}


def test_ida_y_vuelta(tmp_path):
    c = config(tmp_path)
    c.guardar_opciones(Opciones("altura", "primera", "persona", False))
    c.guardar_ritmo(Ritmo(0.5, 2.0, False))
    c.guardar_video("gif", None, 15.0, "media")
    c.sincronizar()
    c2 = config(tmp_path)  # como al volver a abrir el programa
    assert c2.opciones() == Opciones("altura", "primera", "persona", False)
    assert c2.ritmo() == Ritmo(0.5, 2.0, False)
    assert c2.video() == {"formato": "gif", "alto": None, "fps": 15.0, "calidad": "media"}


def test_valores_no_validos(tmp_path):
    (tmp_path / "evoluciona.conf").write_text(
        "[alineacion]\nmetodo=magia\nrecortar=quizas\n[ritmo]\nt_foto=-3\nt_transicion=abc\n"
        "[video]\nformato=avi\nalto=mucho\nfps=0\n")
    c = config(tmp_path)
    assert c.opciones() == Opciones() and c.ritmo() == Ritmo()
    assert c.video() == {"formato": "mp4", "alto": 1080, "fps": 30.0, "calidad": "alta"}


def test_la_linea_de_ordenes_prevalece(tmp_path):
    c = config(tmp_path)
    c.guardar_opciones(Opciones("altura", "primera", "persona", False))
    assert combinar(c.opciones(), {"metodo": "contorno"}) == Opciones("contorno", "primera", "persona", False)
    assert combinar(Ritmo(0.5, 2.0, False), {}) == Ritmo(0.5, 2.0, False)
