"""Opacidad de los contornos según el deslizador de la comparación por pares."""

import pytest

from evoluciona.gui import CONTORNO_ATENUADO, opacidades_contornos


@pytest.mark.parametrize("alfa, esperado", [
    (0.0, (CONTORNO_ATENUADO, 0.0)),   # solo A: su contorno atenuado, el de B oculto
    (0.5, (1.0, 1.0)),                 # mitad y mitad: ambos plenos
    (1.0, (0.0, CONTORNO_ATENUADO)),   # solo B
    (0.25, ((1.0 + CONTORNO_ATENUADO) / 2, 0.5)),
])
def test_opacidades(alfa, esperado):
    assert opacidades_contornos(alfa) == pytest.approx(esperado)


def test_simetria_y_rango():
    for k in range(101):
        a, b = opacidades_contornos(k / 100)
        assert 0.0 <= a <= 1.0 and 0.0 <= b <= 1.0
        assert (b, a) == pytest.approx(opacidades_contornos(1 - k / 100))
