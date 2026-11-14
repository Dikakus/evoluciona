# Evoluciona

Alinea las fotografías de un *timelapse* sobre la figura en primer plano (una persona, una planta,
un objeto…) para poder superponerlas y ver su evolución. Permite compararlas por pares con un
deslizador, reproducirlas como vídeo y exportar el resultado a MP4, WebM o GIF.

## Instalación

Requiere Linux x86_64 o aarch64 (también WSL), Python ≥ 3.10 y `ffmpeg` (solo para generar vídeo).

### Como aplicación de escritorio

```bash
./instalar.sh -n                  # simula: muestra qué se instalaría y dónde, sin tocar nada
./instalar.sh                     # instala en /usr/local (pide confirmación; usa sudo si hace falta)
./instalar.sh usuario@equipo -p 22   # instala en otro equipo por SSH: Linux o Windows con WSL
./instalar.sh -u                  # desinstala
```

Instala el programa en un entorno de Python propio (`/usr/local/lib/evoluciona/`), la orden
`/usr/local/bin/evoluciona`, el lanzador del menú de aplicaciones con su icono y la página de manual
(`man evoluciona`). Instala lo que hay en el último commit de git (se niega si hay cambios sin commit) y
compara versiones para no reinstalar ni volver atrás sin querer (`-f` lo fuerza). Si el equipo remoto es un Windows (su SSH abre `cmd.exe`), lo detecta
y se instala en su distribución WSL por defecto, sin cambiar nada en el Windows. `./instalar.sh -h`
explica todas las opciones (`-d` prefijo, `-m` carpeta de manuales, `-g` GPU...).

**GPU**: si el equipo destino tiene una tarjeta NVIDIA con CUDA 12 (Linux x86_64 o WSL), se instala
`onnxruntime-gpu` con sus librerías CUDA y cuDNN como paquetes de Python, sin tocar el sistema. Además, cada
vez que se usa el programa se comprueba que la GPU funciona de verdad; si no, se usa la CPU y se avisa.
`evoluciona --diagnostico` muestra qué se usará y `--cpu` desactiva la GPU.

### Para desarrollo

```bash
uv venv && uv pip install -e ".[cpu,heic,dev]"    # o [gpu,heic,dev] con una NVIDIA (CUDA 12)
```

El extra `cpu` o `gpu` elige el motor de la segmentación (uno de los dos, no ambos) y `heic` añade soporte
para fotos HEIC/HEIF de móvil. La primera vez que se alinea se descarga el modelo de segmentación
(U2-Net, ~170 MB) a `~/.cache/evoluciona/`.

## Uso

```bash
.venv/bin/evoluciona                       # abre la ventana; las fotos se eligen desde ella
.venv/bin/evoluciona fotos/                # un directorio completo
.venv/bin/evoluciona 'fotos/2026-*.jpg'    # un patrón (admite **)
.venv/bin/evoluciona a.jpg b.jpg c.jpg     # ficheros uno a uno
```

Las fotos se ordenan por la secuencia de su nombre: numérica (`nombre-2` antes que `nombre-10`) o de
fechas, aunque estén escritas sin ceros (`nombre-202612`, `nombre-2026915`, `nombre-20261011`) o como
día-mes-año (`foto 2-9-2026`). La hora que siga a la fecha desempata, `-` y `_` valen igual, y si alguna
foto no lleva fecha en el nombre se coloca por su fecha de captura. Si una fecha compacta es ambigua (`2026111`: 11 de enero o 1 de noviembre),
se elige la más cercana a la fecha de captura del fichero. Con `--orden fecha` se usa la fecha de captura
(EXIF) y con `--orden dado`, el orden en que se indican los ficheros.

### La ventana

- **Deslizador**: transición gradual de la primera foto del par a la segunda.
- **Superponer ambas al 50 %**: muestra las dos a la vez; el deslizador se coloca en el centro y queda desactivado.
- **Anterior / Siguiente** (o ← / →): cambia de par de fotografías.
- **Contornos** (o `C`): dibuja el contorno de la silueta que se compara en cada foto del par, la
  izquierda en rojo y la derecha en verde; los botones de color junto a la casilla permiten cambiarlos.
- **Ir a la foto** (lista de la barra de herramientas o Ctrl+G): salta directamente a cualquier foto.
- **Zoom**: rueda del ratón (sobre el punto señalado), `+` / `−`, `0` para ajustar a la ventana, doble clic
  para ampliar o volver a ajustar; la imagen ampliada se mueve arrastrando o con Mayús+flechas. Al ampliar,
  las fotos visibles se regeneran a resolución completa. La barra de estado muestra la leyenda y el nivel
  de zoom; **Ayuda → Atajos de teclado y ratón** (F1) los resume todos.
- **Modo vídeo**: reproduce toda la secuencia, con botones de inicio, reproducir/pausar (espacio) y final,
  y ajustes de transición (fundido o sin fundido), tiempo por foto y duración del fundido. El nombre del
  fichero se sobreimprime en la imagen (se puede ocultar en **Ver**).
- **Generar vídeo…**: formato (MP4 H.264, MP4 H.265, WebM VP9, GIF), resolución, fps, calidad, transición y tiempos.
- **Alineación…**: método de alineado, foto de referencia, modelo de detección y recorte.

La ventana recuerda entre sesiones la alineación, el ritmo del modo vídeo, las opciones del último vídeo
generado, los contornos y sus colores, la última carpeta y el tamaño de la ventana (`~/.config/Evoluciona/Evoluciona.ini`); lo que se
indique en la línea de órdenes prevalece.

### Generar el vídeo sin ventana

```bash
.venv/bin/evoluciona fotos/ --exportar evolucion.mp4 --transicion fundido --t-transicion 0.8 --t-foto 0.5
.venv/bin/evoluciona fotos/ --exportar evolucion.gif --alto 480 --fps 15 --transicion corte
```

`evoluciona --help` muestra todas las opciones.

## Cómo alinea

1. Un modelo de segmentación (U2-Net) obtiene la silueta de la figura destacada de cada foto.
2. Cada foto se escala y desplaza (sin rotar ni deformar) para que su silueta coincida con la de referencia:
   - `contorno` (por defecto): la escala iguala la altura de la figura a la de la primera foto y la
     posición maximiza el solape de las siluetas (si la figura sale por el borde, el solape corrige
     también la escala);
   - `altura`: iguala la altura de la figura; `area`: iguala su superficie; `ninguno`: no alinea.
3. La referencia es la foto anterior (por defecto, adecuado para cambios graduales) o siempre la primera.
4. El resultado se recorta a la zona común a todas las fotos para que no aparezcan bordes negros.

El encuadre lo fija la primera fotografía. Las siluetas calculadas se guardan en `~/.cache/evoluciona/`
para que volver a abrir las mismas fotos sea inmediato.

## Pruebas

```bash
.venv/bin/python -m pytest
```

## Licencia

[MIT](LICENSE).
