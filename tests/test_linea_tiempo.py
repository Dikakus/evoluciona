import pytest

from evoluciona.linea_tiempo import Ritmo


def test_duracion():
    assert Ritmo(1.0, 0.5, True).duracion(4) == pytest.approx(5.5)
    assert Ritmo(1.0, 0.5, False).duracion(4) == pytest.approx(4.0)
    assert Ritmo().duracion(0) == 0.0


def test_estado_con_fundido():
    r = Ritmo(1.0, 0.5, True)
    assert r.estado(0.0, 3) == (0, 0.0)
    assert r.estado(0.99, 3) == (0, 0.0)
    i, a = r.estado(1.25, 3)
    assert i == 0 and a == pytest.approx(0.5)
    assert r.estado(1.5, 3) == (1, 0.0)
    assert r.estado(r.duracion(3), 3) == (2, 0.0)
    assert r.estado(99, 3) == (2, 0.0)
    assert r.estado(-1, 3) == (0, 0.0)


def test_estado_sin_fundido():
    r = Ritmo(0.5, 2.0, False)
    assert [r.estado(t, 3) for t in (0.0, 0.49, 0.5, 1.2, 1.49)] == [(0, 0.0), (0, 0.0), (1, 0.0), (2, 0.0), (2, 0.0)]


def test_solo_fundido_y_casos_limite():
    r = Ritmo(0.0, 1.0, True)
    i, a = r.estado(1.5, 3)
    assert i == 1 and a == pytest.approx(0.5)
    assert Ritmo(0.0, 1.0, False).estado(5, 3) == (0, 0.0)  # duración nula
    assert Ritmo().estado(5, 1) == (0, 0.0)
    assert r.inicio(2) == pytest.approx(2.0)


def test_alfa_siempre_valido():
    r = Ritmo(0.37, 0.61, True)
    n = 7
    for k in range(0, int(r.duracion(n) * 100) + 5):
        i, a = r.estado(k / 100, n)
        assert 0 <= i < n and 0.0 <= a <= 1.0 and (a == 0.0 or i < n - 1)
