import cv2
import numpy as np
import pytest

from evoluciona import alineacion as al
from evoluciona.linea_tiempo import Ritmo
from evoluciona.proyecto import Opciones, analizar
from evoluciona.video import AjustesVideo, exportar, ffmpeg_disponible


def silueta(ancho, alto, cx, cy, escala):
    """Máscara de una figura tipo persona (cabeza + tronco + piernas)."""
    m = np.zeros((alto, ancho), np.uint8)
    e = escala
    cv2.circle(m, (int(cx), int(cy - 95 * e)), int(22 * e), 255, -1)
    cv2.ellipse(m, (int(cx), int(cy - 20 * e)), (int(38 * e), int(60 * e)), 0, 0, 360, 255, -1)
    cv2.rectangle(m, (int(cx - 30 * e), int(cy + 30 * e)), (int(cx - 6 * e), int(cy + 115 * e)), 255, -1)
    cv2.rectangle(m, (int(cx + 6 * e), int(cy + 30 * e)), (int(cx + 30 * e), int(cy + 115 * e)), 255, -1)
    return m


def test_describir_vacia():
    fig, _ = al.describir(np.zeros((200, 300), np.uint8), 3000, 2000)
    assert fig is None


def test_describir_escala_a_la_foto():
    fig, limpia = al.describir(silueta(320, 240, 160, 120, 0.8), 3200, 2400)
    assert fig is not None and limpia.max() == 1.0
    assert fig.cx == pytest.approx(1600, abs=30)
    assert fig.alto == pytest.approx((115 + 117) * 0.8 * 10, rel=0.06)


@pytest.mark.parametrize("metodo", ["contorno", "altura", "area"])
def test_alinear_recupera_escala_y_posicion(metodo):
    lienzo = (3200, 2400)
    fig0, m0 = al.describir(silueta(320, 240, 150, 120, 0.9), *lienzo)
    t0 = al.cubrir(*lienzo, lienzo)
    assert t0 == al.Similitud(1.0, 0.0, 0.0)
    m, v = al.a_lienzo(m0, t0, lienzo, lienzo)
    ref = al.Referencia(fig0.transformada(t0), m, v)
    # la misma figura, más pequeña y desplazada, en una foto de otro tamaño
    tam = (2000, 3000)
    fig1, m1 = al.describir(silueta(200, 300, 90, 170, 0.6), *tam)
    t = al.alinear(fig1, m1, tam, ref, lienzo, metodo)
    assert t.s == pytest.approx(0.9 / 0.6, rel=0.03)
    assert t.s * 900 + t.tx == pytest.approx(1500, abs=40)
    assert t.s * 1700 + t.ty == pytest.approx(1200, abs=40)


def _foto(ruta, ancho, alto, cx, cy, escala):
    img = np.full((alto, ancho, 3), (70, 110, 60), np.uint8)
    img[silueta(ancho, alto, cx, cy, escala) > 0] = (200, 170, 150)
    cv2.imwrite(str(ruta), img)


def test_sin_alinear_y_exportar(tmp_path):
    """Recorrido completo sin modelo de segmentación (método «ninguno»)."""
    _foto(tmp_path / "a1.png", 320, 240, 160, 120, 0.8)
    _foto(tmp_path / "a2.png", 400, 240, 200, 120, 0.8)
    rutas = [tmp_path / "a1.png", tmp_path / "a2.png"]
    p = analizar(rutas, Opciones(metodo="ninguno"))
    assert p.lienzo == (320, 240) and p.recorte == (0, 0, 320, 240)
    f = p.fotograma(1, (160, 120))
    assert f.shape == (120, 160, 3) and f.mean() > 30
    w, h = p.tam_salida(alto=121, par=True)
    assert w % 2 == 0 and h % 2 == 0 and abs(h - 121) <= 1 and abs(w - 161) <= 1
    if not ffmpeg_disponible():
        pytest.skip("sin ffmpeg")
    salida = exportar(p, tmp_path / "v.mp4", AjustesVideo(fps=10, ritmo=Ritmo(0.2, 0.2, True)))
    assert salida.exists() and salida.stat().st_size > 500
    assert not list(tmp_path.glob(".*parcial*"))
