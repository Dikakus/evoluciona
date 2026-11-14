"""Evoluciona: comparación y vídeo de timelapses alineados sobre la figura en primer plano."""

__version__ = "0.5.0"

try:  # el instalador escribe el commit del que procede la copia instalada
    from ._procedencia import COMMIT as __commit__
except ImportError:
    __commit__ = ""


def version_completa() -> str:
    return f"{__version__} (commit {__commit__})" if __commit__ else f"{__version__} (copia de desarrollo)"
