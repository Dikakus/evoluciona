"""Elección de GPU/CPU en la segmentación, con un onnxruntime simulado (no hace falta GPU ni modelo)."""

import sys
import types

import numpy as np
import pytest

from evoluciona import segmentacion as seg


class Sesion:
    def __init__(self, ruta, sess_options=None, providers=()):
        self.proveedores = list(providers)
        self.falla = None

    def get_providers(self):
        return self.proveedores

    def get_inputs(self):
        return [types.SimpleNamespace(name="x")]

    def run(self, _salidas, entradas):
        if self.falla and self.proveedores[0] == seg.CUDA:
            raise RuntimeError(self.falla)
        return [np.zeros((1, 1, 320, 320), np.float32)]


def ort_simulado(monkeypatch, proveedores, cuda_arranca=True, falla_al_ejecutar=None):
    m = types.ModuleType("onnxruntime")
    m.__version__ = "0-prueba"
    m.get_available_providers = lambda: list(proveedores)
    m.SessionOptions = lambda: types.SimpleNamespace()
    m.preload_dlls = lambda: None

    def crear(ruta, sess_options=None, providers=()):
        p = list(providers)
        if p[0] == seg.CUDA and not cuda_arranca:
            p = p[1:]  # onnxruntime vuelve a la CPU en silencio
        s = Sesion(ruta, sess_options, p)
        s.falla = falla_al_ejecutar
        return s

    m.InferenceSession = crear
    monkeypatch.setitem(sys.modules, "onnxruntime", m)
    monkeypatch.setattr(seg, "ruta_modelo", lambda modelo, aviso=None: "modelo.onnx")
    monkeypatch.setattr(seg, "USAR_GPU", True)
    monkeypatch.delenv("EVOLUCIONA_DISPOSITIVO", raising=False)


def test_sin_cuda_usa_cpu(monkeypatch):
    ort_simulado(monkeypatch, ["CPUExecutionProvider"])
    s = seg.Segmentador()
    assert s.dispositivo == "CPU" and s.aviso_gpu is None


def test_cuda_que_funciona(monkeypatch):
    ort_simulado(monkeypatch, [seg.CUDA, "CPUExecutionProvider"])
    s = seg.Segmentador()
    assert s.dispositivo == "GPU (CUDA)" and s.aviso_gpu is None
    assert s.mascara(np.zeros((40, 60, 3), np.uint8)).shape == (213, 320)


def test_cuda_que_no_arranca(monkeypatch):
    ort_simulado(monkeypatch, [seg.CUDA, "CPUExecutionProvider"], cuda_arranca=False)
    s = seg.Segmentador()
    assert s.dispositivo == "CPU" and "No se pudo usar la GPU" in s.aviso_gpu


def test_cuda_que_falla_en_la_prueba(monkeypatch):
    ort_simulado(monkeypatch, [seg.CUDA, "CPUExecutionProvider"], falla_al_ejecutar="sin memoria")
    s = seg.Segmentador()
    assert s.dispositivo == "CPU" and "sin memoria" in s.aviso_gpu


@pytest.mark.parametrize("forzar", ["opcion", "entorno"])
def test_cpu_forzada(monkeypatch, forzar):
    ort_simulado(monkeypatch, [seg.CUDA, "CPUExecutionProvider"])
    if forzar == "opcion":
        monkeypatch.setattr(seg, "USAR_GPU", False)
    else:
        monkeypatch.setenv("EVOLUCIONA_DISPOSITIVO", "cpu")
    s = seg.Segmentador()
    assert s.dispositivo == "CPU" and s.aviso_gpu is None


def test_gpu_que_falla_a_mitad(monkeypatch):
    ort_simulado(monkeypatch, [seg.CUDA, "CPUExecutionProvider"])
    s = seg.Segmentador()
    s.sesion.falla = "error de CUDA"
    s.mascara(np.zeros((40, 60, 3), np.uint8))
    assert s.dispositivo == "CPU" and "durante el análisis" in s.aviso_gpu
