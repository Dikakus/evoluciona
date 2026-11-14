#!/usr/bin/env bash
# SC2029: las órdenes para ssh se componen a propósito en local (con comillas POSIX mediante q)
# shellcheck disable=SC2029
# Instala Evoluciona como aplicación de escritorio, en este equipo o en otro por SSH.
#
# Instala (con PREFIJO = /usr/local por defecto):
#   PREFIJO/lib/evoluciona/<versión>/          entorno de Python con el programa y sus dependencias
#   PREFIJO/lib/evoluciona/actual              enlace a la versión en uso (se cambia de forma atómica)
#   PREFIJO/bin/evoluciona                     lanzador
#   PREFIJO/share/applications/evoluciona.desktop
#   PREFIJO/share/icons/hicolor/*/apps/evoluciona.{svg,png}
#   MANUALES/evoluciona.1                      (por defecto PREFIJO/share/man/man1)
#
# Lo que se instala sale del último commit de git (no de la copia de trabajo), y la copia instalada
# guarda ese commit: `evoluciona --version` lo muestra.
#
# El equipo remoto puede ser Linux o Windows con WSL: el SSH de Windows abre las sesiones en cmd.exe, así
# que se detecta y todas las órdenes van por «wsl.exe -e». Los scripts nunca viajan por la línea de órdenes
# (cmd.exe interpreta % ^ " & | < >): se copian con tar a una carpeta temporal y se ejecutan desde allí.

set -euo pipefail

VERSION="1.1.0"
NOMBRE="evoluciona"
PROYECTO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ESTE=$(basename "${BASH_SOURCE[0]}")
TAMANOS_ICONO="16 24 32 48 64 128 256"

# códigos de salida
E_ERROR=1 E_USO=2 E_CANCELADO=3

uso() {
    cat <<EOF
Uso: $ESTE [opciones] [[usuario@]equipo]

Instala Evoluciona (programa, lanzador de escritorio, iconos y página de manual) en este equipo o,
si se indica [usuario@]equipo, en otro por SSH (una sola conexión: la contraseña se pide una vez).
El equipo remoto puede ser Linux o un Windows con WSL (se detecta solo y se instala en la
distribución WSL por defecto, con su usuario por defecto).

Opciones:
  -d, --destino PREFIJO   prefijo de instalación (por defecto /usr/local): bin, lib y share van debajo
  -m, --manuales DIR      carpeta de las páginas de manual (por defecto PREFIJO/share/man/man1)
  -p, --puerto N          puerto SSH del equipo remoto
  -g, --gpu MODO          auto (por defecto): usa la GPU si hay una NVIDIA con CUDA 12 en Linux x86_64;
                          si: instala el soporte de GPU igualmente; no: solo CPU
  -u, --desinstalar       desinstala en lugar de instalar
  -f, --forzar            reinstala aunque la versión instalada sea la misma o superior
  -n, --simular           muestra lo que se haría sin tocar el destino
  -s, --si                confirma sin preguntar (obligatorio si no hay terminal)
  -h, --help              muestra esta ayuda
      --version           muestra la versión del instalador

Las rutas de -d y -m se refieren al equipo destino; pueden empezar por ~ (entre comillas) para
referirse a la carpeta personal del usuario destino. Si el destino no es escribible se usa sudo.

Códigos de salida: 0 bien (o ya al día), 1 error, 2 uso incorrecto, 3 cancelado.
EOF
}

msg() { printf '%s\n' "$*"; }
titulo() { printf '\n== %s ==\n' "$*"; }
aviso() { printf 'Aviso: %s\n' "$*" >&2; AVISOS+=("$*"); }
error() { printf 'Error: %s\n' "$*" >&2; exit "$E_ERROR"; }
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }   # comillas para un shell POSIX

confirmar() {  # confirmar "pregunta" -> 0 si sí (por defecto, no)
    if ((SI)); then return 0; fi
    if [[ ! -t 0 ]]; then
        error "no hay terminal para confirmar: usa -s/--si"
    fi
    local r
    read -r -p "$1 [s/N] " r
    [[ $r =~ ^[sSyY] ]]
}

# ------------------------------------------------------------------------------------------- opciones

PREFIJO="/usr/local" PREFIJO_ELEGIDO=0
MANUALES="" MANUALES_ELEGIDA=0
PUERTO="" GPU="auto" DESINSTALAR=0 FORZAR=0 SIMULAR=0 SI=0 EQUIPO=""
AVISOS=()

while (($#)); do
    case $1 in
        -d|--destino) [[ $# -ge 2 ]] || { uso >&2; exit "$E_USO"; }; PREFIJO=$2; PREFIJO_ELEGIDO=1; shift ;;
        -m|--manuales) [[ $# -ge 2 ]] || { uso >&2; exit "$E_USO"; }; MANUALES=$2; MANUALES_ELEGIDA=1; shift ;;
        -p|--puerto) [[ $# -ge 2 && $2 =~ ^[0-9]+$ ]] || { msg "Puerto no válido" >&2; exit "$E_USO"; }; PUERTO=$2; shift ;;
        -g|--gpu) [[ $# -ge 2 && $2 =~ ^(auto|si|no)$ ]] || { msg "--gpu admite auto, si o no" >&2; exit "$E_USO"; }
                  GPU=$2; shift ;;
        -u|--desinstalar) DESINSTALAR=1 ;;
        -f|--forzar) FORZAR=1 ;;
        -n|--simular) SIMULAR=1 ;;
        -s|--si) SI=1 ;;
        -h|--help) uso; exit 0 ;;
        --version) msg "$ESTE $VERSION"; exit 0 ;;
        -*) msg "Opción desconocida: $1" >&2; uso >&2; exit "$E_USO" ;;
        *) [[ -z $EQUIPO ]] || { msg "Sobra el argumento: $1" >&2; exit "$E_USO"; }; EQUIPO=$1 ;;
    esac
    shift
done
[[ -n $MANUALES ]] || MANUALES="$PREFIJO/share/man/man1"
[[ -z $PUERTO || -n $EQUIPO ]] || { msg "-p/--puerto solo tiene sentido con un equipo remoto" >&2; exit "$E_USO"; }
for r in "$PREFIJO" "$MANUALES"; do
    # shellcheck disable=SC2088  # la tilde se expande en el destino, no aquí
    [[ $r == /* || $r == "~" || $r == "~/"* ]] || { msg "La ruta debe ser absoluta (o empezar por ~): $r" >&2; exit "$E_USO"; }
done

TMP=$(mktemp -d "${TMPDIR:-/tmp}/evoluciona-instalar.XXXXXX")
SSH_OPC=()
PREFIJO_REMOTO=()  # (wsl.exe -e) si el remoto es Windows con WSL
WINDOWS=0
RT=""  # carpeta temporal en el destino remoto
limpiar() {
    if [[ -n $EQUIPO && -n $RT ]]; then
        remoto rm -rf "$RT" </dev/null 2>/dev/null || true
    fi
    if [[ -n $EQUIPO && ${#SSH_OPC[@]} -gt 0 ]]; then
        ssh "${SSH_OPC[@]}" -O exit "$EQUIPO" 2>/dev/null || true
    fi
    rm -rf "$TMP"
}
trap limpiar EXIT
trap 'exit "$E_CANCELADO"' INT TERM

# --------------------------------------------------------------------- script que se ejecuta en el destino

escribir_destino() {
cat > "$TMP/destino.sh" <<'DESTINO'
# Parte del instalador de Evoluciona que se ejecuta en el equipo destino (shell POSIX).
set -eu

# shellcheck disable=SC2088  # se compara con la tilde literal para expandirla aquí
expandir() { case $1 in "~") printf '%s' "$HOME" ;; "~/"*) printf '%s/%s' "$HOME" "${1#\~/}" ;; *) printf '%s' "$1" ;; esac; }

escribible() {  # ¿se puede escribir en la carpeta (o en su antecesor existente más cercano)?
    d=$1
    while [ ! -e "$d" ]; do d=$(dirname "$d"); done
    [ -w "$d" ]
}

como() {  # como CARPETA orden...: ejecuta con sudo si la carpeta no es escribible
    d=$1; shift
    if escribible "$d"; then "$@"; else sudo "$@"; fi
}

buscar_uv() {
    for c in "$(command -v uv 2>/dev/null || true)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -n "$c" ] && [ -x "$c" ]; then printf '%s' "$c"; return; fi
    done
}

ficheros() {  # ficheros instalables (rutas absolutas), uno por línea
    printf '%s\n' "$1/bin/evoluciona" "$1/share/applications/evoluciona.desktop" \
        "$1/share/icons/hicolor/scalable/apps/evoluciona.svg"
    for t in 16 24 32 48 64 128 256; do printf '%s\n' "$1/share/icons/hicolor/${t}x$t/apps/evoluciona.png"; done
    printf '%s\n' "$2/evoluciona.1"
}

LIBRERIAS="libGL.so.1=libgl1 libEGL.so.1=libegl1 libOpenGL.so.0=libopengl0 libfontconfig.so.1=libfontconfig1
libglib-2.0.so.0=libglib2.0-0t64|libglib2.0-0 libdbus-1.so.3=libdbus-1-3 libxkbcommon.so.0=libxkbcommon0
libxkbcommon-x11.so.0=libxkbcommon-x11-0 libxcb-cursor.so.0=libxcb-cursor0 libxcb-icccm.so.4=libxcb-icccm4
libxcb-image.so.0=libxcb-image0 libxcb-keysyms.so.1=libxcb-keysyms1 libxcb-randr.so.0=libxcb-randr0
libxcb-render-util.so.0=libxcb-render-util0 libxcb-shape.so.0=libxcb-shape0 libxcb-xfixes.so.0=libxcb-xfixes0
libxcb-xkb.so.1=libxcb-xkb1 libwayland-client.so.0=libwayland-client0 libwayland-cursor.so.0=libwayland-cursor0
libwayland-egl.so.1=libwayland-egl1"

sin_espacios() {
    case "$1$2" in *[[:space:]]*) echo "Error: las rutas de instalación no pueden contener espacios" >&2; exit 2 ;; esac
}

consultar() {
    P=$(expandir "$1"); M=$(expandir "$2")
    sin_espacios "$P" "$M"
    echo "prefijo=$P"
    echo "manuales=$M"
    echo "arq=$(uname -m)"
    echo "so=$( (. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-}") || true)"
    if grep -qi microsoft /proc/version 2>/dev/null; then echo "wsl=si"; else echo "wsl=no"; fi
    PY=$(command -v python3 2>/dev/null || true)
    echo "python=$PY"
    if [ -n "$PY" ]; then
        echo "python_version=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
        if "$PY" -c 'import venv, ensurepip' 2>/dev/null; then echo "venv=si"; else echo "venv=no"; fi
    fi
    echo "uv=$(buscar_uv)"
    # en WSL nvidia-smi está en /usr/lib/wsl/lib, que no suele estar en el PATH de una sesión SSH
    NVSMI=$(command -v nvidia-smi 2>/dev/null || true)
    if [ -z "$NVSMI" ] && [ -x /usr/lib/wsl/lib/nvidia-smi ]; then NVSMI=/usr/lib/wsl/lib/nvidia-smi; fi
    if [ -n "$NVSMI" ] && "$NVSMI" -L >/dev/null 2>&1 </dev/null; then
        echo "gpu=$("$NVSMI" --query-gpu=name --format=csv,noheader 2>/dev/null </dev/null | head -n 1)"
        echo "cuda=$("$NVSMI" 2>/dev/null </dev/null | sed -n 's/.*CUDA[A-Z ]* Version: *\([0-9][0-9.]*\).*/\1/p' | head -n 1)"  # «CUDA Version» o «CUDA UMD Version»
    fi
    echo "ffmpeg=$(command -v ffmpeg 2>/dev/null || true)"
    echo "apt=$(command -v apt-get 2>/dev/null || true)"
    if command -v sudo >/dev/null 2>&1; then echo "sudo=si"; else echo "sudo=no"; fi
    if sudo -n true 2>/dev/null; then echo "sudo_sin_clave=si"; else echo "sudo_sin_clave=no"; fi
    necesita=no
    for d in "$P/lib" "$P/bin" "$P/share" "$M"; do escribible "$d" || necesita=si; done
    echo "distro=${WSL_DISTRO_NAME:-}"
    echo "usuario=$(id -un)"
    echo "necesita_sudo=$necesita"

    L="$P/lib/evoluciona"
    if [ -r "$L/actual/VERSION" ]; then echo "inst_programa=$(cat "$L/actual/VERSION")"; fi
    if [ -d "$L" ]; then
        activa=$(readlink "$L/actual" 2>/dev/null || true)
        for v in "$L"/*; do
            if [ -d "$v" ] && [ ! -L "$v" ] && [ "$(basename "$v")" != "$activa" ]; then echo "antigua=$v"; fi
        done
        echo "entorno_mb=$(du -sm "$L" 2>/dev/null | cut -f1)"
    fi
    if [ -e "$P/bin/evoluciona" ] || [ -L "$P/bin/evoluciona" ]; then
        if grep -q 'Lanzador de Evoluciona' "$P/bin/evoluciona" 2>/dev/null; then echo "lanzador=propio"; else echo "lanzador=ajeno"; fi
    fi
    f="$P/share/applications/evoluciona.desktop"
    if [ -r "$f" ]; then echo "inst_escritorio=$(sed -n 's/^X-Evoluciona-Version=//p' "$f" | head -n 1)"; fi
    f="$M/evoluciona.1"
    if [ -r "$f" ]; then echo "inst_manual=$(sed -n 's/^\.TH .*"evoluciona \([^"]*\)".*/\1/p' "$f" | head -n 1)"; fi
    for f in $(ficheros "$P" "$M"); do
        if [ -e "$f" ]; then echo "existe=$f"; elif [ "${f%.png}" != "$f" ] || [ "${f%.svg}" != "$f" ]; then echo "falta_icono=$f"; fi
    done

    # librerías del sistema que necesita Qt (PySide6)
    LDC=$(command -v ldconfig 2>/dev/null || echo /sbin/ldconfig)
    CACHE=$("$LDC" -p 2>/dev/null || true)
    for par in $LIBRERIAS; do
        so=${par%%=*}; candidatos=${par#*=}
        if ! printf '%s\n' "$CACHE" | grep -q "[[:space:]]${so}[[:space:]]"; then
            paquete=""
            if command -v apt-cache >/dev/null 2>&1; then
                IFS='|'
                for c in $candidatos; do
                    if apt-cache show "$c" >/dev/null 2>&1 </dev/null; then paquete=$c; break; fi
                done
                unset IFS
            fi
            echo "falta_libreria=$so:${paquete:-?}"
        fi
    done

    # entorno de la sesión del usuario: PATH, manpath y carpetas de datos XDG
    RUTA=$("${SHELL:-sh}" -lc 'printf %s "$PATH"' 2>/dev/null </dev/null || printf %s "$PATH")
    case ":$RUTA:" in *":$P/bin:"*) echo "en_path=si" ;; *) echo "en_path=no" ;; esac
    if command -v manpath >/dev/null 2>&1; then
        case ":$(manpath 2>/dev/null):" in *":$(dirname "$M"):"*) echo "en_manpath=si" ;; *) echo "en_manpath=no" ;; esac
    else
        echo "en_manpath=?"
    fi
    case ":${XDG_DATA_DIRS:-/usr/local/share:/usr/share}:$HOME/.local/share:" in
        *":$P/share:"*) echo "en_xdg=si" ;; *) echo "en_xdg=no" ;; esac
}

instalar_fichero() {  # ORIGEN DESTINO MODO: copia atómica (temporal en el destino + mv)
    d=$(dirname "$2")
    como "$d" mkdir -p "$d"
    t="$d/.$(basename "$2").nuevo.$$"
    como "$d" cp "$1" "$t"
    como "$d" chmod "$3" "$t"
    como "$d" mv -f "$t" "$2"
}

instalar_programa() {  # P CARGA VERSION COMMIT MODO
    P=$1; C=$2; V=$3; COMMIT=$4; MODO=$5
    L="$P/lib/evoluciona"
    RUEDA=$(ls "$C"/evoluciona-*.whl)
    PY=$(command -v python3)
    UV=$(buscar_uv)
    nuevo="$L/$V-$COMMIT-$(date +%Y%m%d%H%M%S)"
    como "$L" mkdir -p "$L"

    crear() {  # crear EXTRAS
        echo "Creando el entorno de Python en $nuevo (extras: $1)…"
        if [ -n "$UV" ]; then
            como "$L" env UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never "$UV" venv -q --python "$PY" "$nuevo" &&
            como "$L" env UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never "$UV" pip install -q --python "$nuevo/bin/python" \
                "evoluciona[$1] @ file://$RUEDA"
        else
            como "$L" "$PY" -m venv "$nuevo" &&
            como "$L" "$nuevo/bin/python" -m pip install -q --no-cache-dir --disable-pip-version-check \
                "evoluciona[$1] @ file://$RUEDA"
        fi
    }

    if [ "$MODO" = gpu ]; then
        if ! crear gpu,heic; then
            echo "Aviso: no se pudo instalar el soporte de GPU; se instala la versión para CPU." >&2
            como "$L" rm -rf "$nuevo"; MODO=cpu
        fi
    fi
    if [ "$MODO" = cpu ] && ! crear cpu,heic; then
        como "$L" rm -rf "$nuevo"
        echo "Error: no se pudo crear el entorno de Python" >&2; exit 1
    fi

    # comprobación antes de activar la nueva versión: si falla, la instalada sigue intacta
    if ! QT_QPA_PLATFORM=offscreen "$nuevo/bin/python" -c \
            'import PySide6.QtWidgets, cv2, onnxruntime, evoluciona.gui, evoluciona.cli' 2>"$C/import.err"; then
        cat "$C/import.err" >&2
        como "$L" rm -rf "$nuevo"
        echo "Error: el programa instalado no arranca (¿faltan librerías del sistema?); no se ha activado" >&2
        exit 1
    fi
    for plugin in "$nuevo"/lib/python*/site-packages/PySide6/Qt/plugins/platforms/libq*.so; do
        [ -e "$plugin" ] || continue
        faltan=$(ldd "$plugin" 2>/dev/null | sed -n 's/^[[:space:]]*\([^ ]*\) => not found.*/\1/p' | tr '\n' ' ')
        [ -z "$faltan" ] || echo "Aviso: a $(basename "$plugin") le faltan librerías: $faltan" >&2
    done

    t=$(mktemp "$C/version.XXXXXX")
    printf '%s %s %s\n' "$V" "$COMMIT" "$MODO" > "$t"
    como "$L" cp "$t" "$nuevo/VERSION"
    como "$L" chmod 644 "$nuevo/VERSION"
    # cambio atómico de la versión activa
    como "$L" ln -sfn "$(basename "$nuevo")" "$L/.actual.nuevo"
    como "$L" mv -Tf "$L/.actual.nuevo" "$L/actual"
    for v in "$L"/*; do
        if [ -d "$v" ] && [ ! -L "$v" ] && [ "$v" != "$nuevo" ]; then
            echo "Borrando la versión anterior $(basename "$v")"
            como "$L" rm -rf "$v"
        fi
    done
    instalar_fichero "$C/evoluciona" "$P/bin/evoluciona" 755
    echo "Programa instalado: $V ($COMMIT, $MODO)"
}

instalar_escritorio() {  # P CARGA
    P=$1; C=$2
    instalar_fichero "$C/evoluciona.desktop" "$P/share/applications/evoluciona.desktop" 644
    instalar_fichero "$C/iconos/evoluciona.svg" "$P/share/icons/hicolor/scalable/apps/evoluciona.svg" 644
    for t in 16 24 32 48 64 128 256; do
        instalar_fichero "$C/iconos/evoluciona-$t.png" "$P/share/icons/hicolor/${t}x$t/apps/evoluciona.png" 644
    done
    actualizar_caches "$P"
    echo "Lanzador de escritorio e iconos instalados"
}

actualizar_caches() {  # solo se actualizan las cachés que ya existían, para no dejar ficheros nuevos
    H="$1/share/icons/hicolor"
    if [ -e "$H/icon-theme.cache" ] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
        como "$H" gtk-update-icon-cache -q -t -f "$H" || true
    fi
    A="$1/share/applications"
    if [ -e "$A/mimeinfo.cache" ] && command -v update-desktop-database >/dev/null 2>&1; then
        como "$A" update-desktop-database -q "$A" || true
    fi
}

instalar_manual() {  # M CARGA
    instalar_fichero "$2/evoluciona.1" "$1/evoluciona.1" 644
    echo "Página de manual instalada en $1"
}

desinstalar() {  # P M QUITAR_LANZADOR_AJENO(si/no)
    P=$(expandir "$1"); M=$(expandir "$2")
    sin_espacios "$P" "$M"
    for f in $(ficheros "$P" "$M"); do
        if [ ! -e "$f" ] && [ ! -L "$f" ]; then continue; fi
        if [ "$f" = "$P/bin/evoluciona" ] && ! grep -q 'Lanzador de Evoluciona' "$f" 2>/dev/null && [ "$3" != si ]; then
            echo "Se conserva $f: no es el lanzador de Evoluciona" >&2; continue
        fi
        como "$(dirname "$f")" rm -f "$f"
        echo "Borrado $f"
    done
    if [ -d "$P/lib/evoluciona" ]; then como "$P/lib" rm -rf "$P/lib/evoluciona"; echo "Borrado $P/lib/evoluciona"; fi
    for t in scalable 16x16 24x24 32x32 48x48 64x64 128x128 256x256; do  # carpetas de iconos que queden vacías
        d="$P/share/icons/hicolor/$t"
        if [ -d "$d/apps" ] && como "$d" rmdir "$d/apps" 2>/dev/null; then como "$d" rmdir "$d" 2>/dev/null || true; fi
    done
    actualizar_caches "$P"
}

instalar() {  # COMPONENTES(programa,escritorio,manual) P M CARGA VERSION COMMIT MODO: todo en una llamada
    # (una sola sesión: si sudo pide contraseña, la pide una vez)
    case ",$1," in *,programa,*) instalar_programa "$2" "$4" "$5" "$6" "$7" ;; esac
    case ",$1," in *,escritorio,*) instalar_escritorio "$2" "$4" ;; esac
    case ",$1," in *,manual,*) instalar_manual "$3" "$4" ;; esac
}

accion=$1; shift
case $accion in
    instalar) instalar "$@" ;;
    consultar) consultar "$@" ;;
    desinstalar) desinstalar "$@" ;;
    diagnostico) "$1/bin/evoluciona" --diagnostico ;;
    *) echo "acción desconocida: $accion" >&2; exit 2 ;;
esac
DESTINO
}

# ----------------------------------------------------------------------------- ejecución en el destino

conectar() {
    [[ -n $EQUIPO ]] || return 0
    command -v ssh >/dev/null || error "falta ssh"
    SSH_OPC=(-o ControlMaster=auto -o "ControlPath=$TMP/ssh-%C" -o ControlPersist=600)
    [[ -z $PUERTO ]] || SSH_OPC+=(-p "$PUERTO")
    msg "Conectando con $EQUIPO${PUERTO:+ (puerto $PUERTO)}…"
    # conexión maestra sin ejecutar nada: aún no se sabe qué shell hay al otro lado
    ssh "${SSH_OPC[@]}" -o ControlMaster=yes -o ConnectTimeout=15 -fN "$EQUIPO" \
        || error "no se puede conectar por SSH con $EQUIPO${PUERTO:+ (puerto $PUERTO)}"
    local sistema
    sistema=$(timeout 30 ssh "${SSH_OPC[@]}" "$EQUIPO" uname -sm </dev/null 2>/dev/null | tr -d '\r' || true)
    if [[ $sistema != Linux* ]]; then
        msg "No responde un shell de Linux; probando Windows con WSL (si WSL estaba parado, tarda unos segundos)…"
        sistema=$(timeout 120 ssh "${SSH_OPC[@]}" "$EQUIPO" wsl.exe -e uname -sm </dev/null 2>/dev/null | tr -d '\r' || true)
        [[ $sistema == Linux* ]] || error "$EQUIPO no es Linux ni un Windows con WSL que responda por SSH.
Prueba a mano: ssh${PUERTO:+ -p $PUERTO} $EQUIPO wsl.exe -e uname -a"
        WINDOWS=1 PREFIJO_REMOTO=(wsl.exe -e)
    fi
    RT=$(remoto mktemp -d /tmp/evoluciona-instalar.XXXXXX </dev/null | tr -d '\r' || true)
    if [[ $RT != /tmp/evoluciona-instalar.* ]]; then
        RT=""; error "no se pudo crear la carpeta temporal en $EQUIPO"
    fi
    tar -C "$TMP" -cf - destino.sh | remoto tar -xf - -C "$RT" || error "no se pudo copiar el instalador a $EQUIPO"
}

remoto() {  # remoto [-t] ORDEN ARGS...: ejecuta una orden (sin shell intermedio en Windows) en el remoto
    local t=(-T) a orden=""
    if [[ $1 == -t ]]; then shift; if [[ -t 0 && -t 1 ]]; then t=(-t); fi; fi
    if ((WINDOWS)); then
        # cmd.exe y wsl.exe reciben las palabras tal cual: nada que cmd.exe pueda interpretar
        for a in "$@"; do
            [[ -n $a && $a != *[[:space:]%\"^\&\|\<\>\'\!\(\)]* ]] \
                || error "este valor no se puede enviar a Windows (espacios o caracteres especiales): «$a»"
        done
        ssh "${SSH_OPC[@]}" "${t[@]}" "$EQUIPO" "${PREFIJO_REMOTO[@]}" "$@"
    else
        for a in "$@"; do orden+="$(q "$a") "; done
        ssh "${SSH_OPC[@]}" "${t[@]}" "$EQUIPO" "$orden"
    fi
}

destino() {  # destino ACCION ARGS...: ejecuta la parte del instalador que corre en el destino
    if [[ -z $EQUIPO ]]; then
        sh "$TMP/destino.sh" "$@"
    elif [[ $1 != consultar && $1 != diagnostico && ${D[necesita_sudo]:-} == si && ${D[sudo_sin_clave]:-} != si ]]; then
        remoto -t sh "$RT/destino.sh" "$@"  # con terminal: sudo pedirá la contraseña
    else
        remoto sh "$RT/destino.sh" "$@" </dev/null  # sin terminal: la salida se analiza
    fi
}

consultar_destino() {
    destino consultar "$PREFIJO" "$MANUALES" > "$TMP/consulta" || error "no se pudo consultar el equipo destino"
    leer_consulta
}

en_destino() {  # en_destino ORDEN ARGS...: orden en el destino, con terminal (p. ej. sudo apt-get)
    if [[ -n $EQUIPO ]]; then remoto -t "$@"; else "$@"; fi
}

# ------------------------------------------------------------------------------- comprobaciones locales

version_proyecto() {
    sed -n 's/^__version__ = "\(.*\)"/\1/p' "$PROYECTO/src/evoluciona/__init__.py"
}

comprobar_git() {
    command -v git >/dev/null || error "falta git"
    git -C "$PROYECTO" rev-parse --git-dir >/dev/null 2>&1 || error "$PROYECTO no es un repositorio git"
    if [[ -n $(git -C "$PROYECTO" status --porcelain) ]]; then
        git -C "$PROYECTO" status --short >&2
        error "hay cambios sin commit: haz commit antes de instalar"
    fi
    PVER=$(version_proyecto)
    [[ -n $PVER ]] || error "no se encuentra la versión en src/evoluciona/__init__.py"
    local pp man
    pp=$(sed -n 's/^version = "\(.*\)"/\1/p' "$PROYECTO/pyproject.toml")
    man=$(sed -n 's/^\.TH .*"evoluciona \([^"]*\)".*/\1/p' "$PROYECTO/man/evoluciona.1")
    [[ $pp == "$PVER" ]] || error "la versión de pyproject.toml ($pp) no coincide con la del programa ($PVER)"
    [[ $man == "$PVER" ]] || error "la versión de man/evoluciona.1 ($man) no coincide con la del programa ($PVER)"
    COMMIT=$(git -C "$PROYECTO" rev-parse --short HEAD)

    # si algo cambió desde la etiqueta de su versión sin subirla, no se instala
    local etiqueta
    for etiqueta in "$NOMBRE-v$PVER:src pyproject.toml man datos" "instalar-v$VERSION:instalar.sh"; do
        local nombre=${etiqueta%%:*} rutas
        read -r -a rutas <<< "${etiqueta#*:}"
        if git -C "$PROYECTO" rev-parse -q --verify "refs/tags/$nombre" >/dev/null; then
            if ! git -C "$PROYECTO" diff --quiet "$nombre" HEAD -- "${rutas[@]}"; then
                error "${rutas[*]} cambió desde la etiqueta $nombre sin subir la versión"
            fi
        else
            aviso "no existe la etiqueta $nombre (créala al hacer commit de esa versión)"
        fi
    done
}

preparar_carga() {  # construye en $TMP/carga lo que se copia al destino
    local arbol="$TMP/arbol" carga="$TMP/carga"
    mkdir -p "$arbol" "$carga/iconos"
    git -C "$PROYECTO" archive HEAD | tar -x -C "$arbol"
    printf 'COMMIT = "%s"\n' "$COMMIT" > "$arbol/src/evoluciona/_procedencia.py"
    rm -f "$arbol/.gitignore"  # hatchling excluiría _procedencia.py (está en .gitignore)

    msg "Comprobando la sintaxis…"
    python3 -m compileall -q "$arbol/src" >/dev/null || error "errores de sintaxis en el código Python"
    if command -v groff >/dev/null; then
        groff -k -man -ww -z "$arbol/man/evoluciona.1" 2>"$TMP/groff.err" || true
        [[ ! -s $TMP/groff.err ]] || { cat "$TMP/groff.err" >&2; error "la página de manual tiene avisos de groff"; }
    else
        aviso "no hay groff: no se comprueba la página de manual"
    fi
    sh -n "$TMP/destino.sh" || error "error de sintaxis en la parte del destino"
    if command -v shellcheck >/dev/null; then
        shellcheck -s sh -S warning "$TMP/destino.sh" || error "shellcheck encuentra problemas en la parte del destino"
    fi

    msg "Construyendo el paquete desde el commit $COMMIT…"
    if command -v uv >/dev/null; then
        uv build -q --wheel --out-dir "$carga" "$arbol" || error "no se pudo construir el paquete"
    else
        python3 -m pip wheel -q --no-deps -w "$carga" "$arbol" || error "no se pudo construir el paquete (instala uv o pip)"
    fi
    python3 - "$carga"/evoluciona-*.whl <<'EOF' || error "el paquete no contiene el commit de procedencia"
import sys, zipfile
assert "evoluciona/_procedencia.py" in zipfile.ZipFile(sys.argv[1]).namelist()
EOF
    cp "$arbol/man/evoluciona.1" "$carga/"
    cp "$arbol/src/evoluciona/recursos/evoluciona.svg" "$carga/iconos/"
    local t
    for t in $TAMANOS_ICONO; do cp "$arbol/datos/iconos/evoluciona-$t.png" "$carga/iconos/"; done
}

generar_lanzadores() {  # con el prefijo ya expandido en el destino
    local p=${D[prefijo]} carga="$TMP/carga" icono=evoluciona
    cat > "$carga/evoluciona" <<EOF
#!/bin/sh
# Lanzador de Evoluciona $PVER (generado por $ESTE; no editar)
exec "$p/lib/evoluciona/actual/bin/evoluciona" "\$@"
EOF
    sh -n "$carga/evoluciona" || error "error de sintaxis en el lanzador"
    if command -v shellcheck >/dev/null; then shellcheck -s sh "$carga/evoluciona" || error "shellcheck: lanzador"; fi
    # fuera de las carpetas de datos XDG el tema de iconos no lo encontraría por nombre
    [[ ${D[en_xdg]} == si ]] || icono="$p/share/icons/hicolor/scalable/apps/evoluciona.svg"
    cat > "$carga/evoluciona.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Evoluciona
GenericName=Comparador de timelapses
Comment=Alinea las fotos de un timelapse sobre la figura, compáralas y genera un vídeo
Exec=$p/bin/evoluciona %F
TryExec=$p/bin/evoluciona
Icon=$icono
Terminal=false
Categories=Graphics;Photography;
MimeType=image/jpeg;image/png;image/webp;image/bmp;image/tiff;image/heic;image/heif;inode/directory;
Keywords=timelapse;foto;fotografía;alinear;comparar;vídeo;evolución;
StartupWMClass=evoluciona
X-Evoluciona-Version=$PVER
EOF
    if command -v desktop-file-validate >/dev/null; then
        desktop-file-validate "$carga/evoluciona.desktop" || error "el fichero .desktop no es válido"
    fi
}

# ------------------------------------------------------------------------------------- versiones

declare -A D=()
leer_consulta() {
    local k v
    EXISTEN=() ANTIGUAS=() FALTAN_LIB=() FALTAN_ICONOS=()
    while IFS='=' read -r k v; do
        case $k in
            existe) EXISTEN+=("$v") ;;
            antigua) ANTIGUAS+=("$v") ;;
            falta_libreria) FALTAN_LIB+=("$v") ;;
            falta_icono) FALTAN_ICONOS+=("$v") ;;
            ?*) D[$k]=$v ;;
        esac
    done < "$TMP/consulta"
    for k in prefijo manuales arq so wsl python python_version venv uv gpu cuda ffmpeg apt sudo sudo_sin_clave \
             necesita_sudo distro usuario inst_programa entorno_mb lanzador inst_escritorio inst_manual en_path en_manpath en_xdg; do
        D[$k]=${D[$k]:-}
    done
}

comparar() {  # comparar INSTALADA NUEVA -> ausente | menor | igual | mayor
    if [[ -z $1 ]]; then echo ausente
    elif [[ $1 == "$2" ]]; then echo igual
    elif [[ $(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n 1) == "$1" ]]; then echo menor
    else echo mayor
    fi
}

decidir() {  # decidir COMPONENTE INSTALADA NUEVA [COMMIT_INST] -> deja la decisión en ACCION[COMPONENTE]
    local c=$1 inst=$2 nueva=$3 commit_inst=${4:-} r
    r=$(comparar "$inst" "$nueva")
    case $r in
        ausente) ACCION[$c]="instalar"; MOTIVO[$c]="no instalado" ;;
        menor) ACCION[$c]="instalar"; MOTIVO[$c]="actualizar $inst → $nueva" ;;
        igual)
            if [[ -n $commit_inst && $commit_inst != "$COMMIT" ]]; then
                ACCION[$c]="omitir"; MOTIVO[$c]="misma versión $inst pero de otro commit ($commit_inst)"
            else
                ACCION[$c]="aldia"; MOTIVO[$c]="al día ($inst)"
            fi ;;
        mayor) ACCION[$c]="omitir"; MOTIVO[$c]="la instalada ($inst) es superior a $nueva" ;;
    esac
    if [[ ${ACCION[$c]} != instalar ]] && ((FORZAR)); then
        ACCION[$c]="instalar"; MOTIVO[$c]+="; se reinstala por --forzar"
    fi
}

# ------------------------------------------------------------------------------------- principal

escribir_destino
if ((!DESINSTALAR)); then
    comprobar_git
    preparar_carga
fi
conectar
consultar_destino
DONDE=${EQUIPO:-este equipo}
P=${D[prefijo]} M=${D[manuales]}

titulo "Destino: $DONDE"
if ((WINDOWS)); then
    msg "Sistema: Windows con WSL → distribución «${D[distro]:-?}» (${D[so]:-?}, ${D[arq]}), usuario ${D[usuario]}"
else
    msg "Sistema: ${D[so]:-?} (${D[arq]})$([[ ${D[wsl]} == si ]] && echo ', WSL'), usuario ${D[usuario]}"
fi
msg "Prefijo:   $P $( ((PREFIJO_ELEGIDO)) && echo '(elegido con -d)' || echo '(por defecto)')"
msg "Manuales:  $M $( ((MANUALES_ELEGIDA)) && echo '(elegida con -m)' || echo '(por defecto: PREFIJO/share/man/man1)')"
[[ ${D[necesita_sudo]} != si ]] || msg "Permisos:  se usará sudo (el destino no es escribible por el usuario)"

# --------------------------------------------------------------------------------- desinstalación
if ((DESINSTALAR)); then
    titulo "Se borrará"
    hay=0
    for f in "${EXISTEN[@]}"; do
        if [[ $f == "$P/bin/evoluciona" && ${D[lanzador]:-} == ajeno ]] && ((!FORZAR)); then
            msg "  (se conserva $f: no es el lanzador de Evoluciona; -f para borrarlo)"
        else
            msg "  $f"; hay=1
        fi
    done
    if [[ -n ${D[entorno_mb]:-} ]]; then msg "  $P/lib/evoluciona/  (entorno de Python, ${D[entorno_mb]} MB)"; hay=1; fi
    if ((!hay)); then msg "Evoluciona no está instalado en $DONDE con ese prefijo."; exit 0; fi
    msg "No se borran los modelos ni las siluetas de ~/.cache/evoluciona/ (de cada usuario)."
    if ((SIMULAR)); then msg $'\nSimulación: no se ha cambiado nada.'; exit 0; fi
    confirmar "¿Desinstalar Evoluciona de $DONDE?" || { msg "Cancelado."; exit "$E_CANCELADO"; }
    destino desinstalar "$PREFIJO" "$MANUALES" "$( ((FORZAR)) && echo si || echo no)" \
        || error "la desinstalación no terminó bien"
    titulo "Resumen"
    msg "Evoluciona desinstalado de $DONDE."
    exit 0
fi

# ------------------------------------------------------------------------------------ dependencias
[[ ${D[arq]} == x86_64 || ${D[arq]} == aarch64 ]] \
    || error "arquitectura ${D[arq]} no compatible: PySide6 y onnxruntime solo tienen paquetes para x86_64 y aarch64 (un sistema de 32 bits no sirve)"
FALTAN=() OPC=()
pv=${D[python_version]:-}
if [[ -z ${D[python]:-} ]]; then FALTAN+=("python3")
elif [[ $(printf '%s\n3.10\n' "$pv" | sort -V | head -n 1) != 3.10 ]]; then
    error "Python ${pv:-?} en el destino; hace falta 3.10 o superior"
fi
[[ -n ${D[uv]:-} || ${D[venv]:-} == si ]] || FALTAN+=("python3-venv")
for l in "${FALTAN_LIB[@]}"; do
    paquete=${l#*:}
    [[ $paquete != "?" ]] || { aviso "falta la librería ${l%%:*} y no se sabe qué paquete la contiene"; continue; }
    [[ " ${FALTAN[*]} " == *" $paquete "* ]] || FALTAN+=("$paquete")
done
[[ -n ${D[ffmpeg]:-} ]] || OPC+=("ffmpeg")

titulo "Dependencias en $DONDE"
if [[ -n ${D[uv]} ]]; then herramienta="uv (${D[uv]})"; else herramienta="python3 -m venv"; fi
msg "Python: ${D[python]:-no hay} ${pv}; el entorno se crea con $herramienta"
msg "ffmpeg (opcional, para generar vídeo): ${D[ffmpeg]:-no instalado}"
if [[ -n ${D[gpu]:-} ]]; then msg "GPU: ${D[gpu]} (CUDA ${D[cuda]:-?})"; else msg "GPU NVIDIA: no detectada"; fi
if ((${#FALTAN[@]})); then msg "Faltan (obligatorias): ${FALTAN[*]}"; fi
if ((${#OPC[@]})); then msg "Faltan (opcionales): ${OPC[*]}"; fi

if ((${#FALTAN[@]} + ${#OPC[@]})); then
    if [[ -z ${D[apt]:-} ]]; then
        ((${#FALTAN[@]} == 0)) || error "faltan dependencias y el destino no tiene apt-get: instala ${FALTAN[*]}"
    elif ((SIMULAR)); then
        msg "(se ofrecería instalarlas con: sudo apt-get install ${FALTAN[*]} ${OPC[*]})"
    else
        paquetes=("${FALTAN[@]}")
        if ((${#OPC[@]})) && confirmar "¿Instalar también las opcionales (${OPC[*]})?"; then paquetes+=("${OPC[@]}"); fi
        if ((${#paquetes[@]})); then
            if confirmar "¿Instalar en $DONDE con «sudo apt-get install ${paquetes[*]}»?"; then
                en_destino sudo apt-get install -y "${paquetes[@]}" \
                    || error "no se pudieron instalar las dependencias"
                consultar_destino
                FALTAN=()
            elif ((${#FALTAN[@]})); then
                error "sin estas dependencias no se puede instalar: ${FALTAN[*]}"
            fi
        fi
    fi
fi

# ------------------------------------------------------------------------------------------- GPU
MODO=cpu MOTIVO_GPU=""
cuda_mayor=${D[cuda]%%.*}  # D tiene todas las claves (vacías si no se informaron)
case $GPU in
    no) MOTIVO_GPU="desactivada con --gpu no" ;;
    si) [[ ${D[arq]} == x86_64 ]] || error "--gpu si: onnxruntime-gpu solo existe para Linux x86_64"
        MODO=gpu; MOTIVO_GPU="pedida con --gpu si" ;;
    auto)
        if [[ -z ${D[gpu]:-} ]]; then MOTIVO_GPU="no hay GPU NVIDIA (nvidia-smi)"
        elif [[ ${D[arq]} != x86_64 ]]; then MOTIVO_GPU="onnxruntime-gpu no existe para ${D[arq]}"
        elif [[ -z $cuda_mayor || $cuda_mayor -lt 12 ]]; then MOTIVO_GPU="el controlador admite CUDA ${D[cuda]:-?}; hace falta 12 o superior"
        else MODO=gpu; MOTIVO_GPU="${D[gpu]}, CUDA ${D[cuda]}"
        fi ;;
esac

# ------------------------------------------------------------------------------------------- plan
declare -A ACCION=() MOTIVO=()
read -r inst_v inst_commit inst_modo <<< "${D[inst_programa]:-}"
decidir programa "${inst_v:-}" "$PVER" "${inst_commit:-}"
if [[ ${ACCION[programa]} == aldia && -n ${inst_modo:-} && $inst_modo != "$MODO" ]]; then
    aviso "la versión instalada usa ${inst_modo^^} y ahora correspondería ${MODO^^} ($MOTIVO_GPU): reinstala con -f para cambiarlo"
fi
if [[ ${ACCION[programa]} == aldia && ${D[lanzador]:-} != propio ]]; then
    ACCION[programa]=instalar; MOTIVO[programa]="falta el lanzador $P/bin/evoluciona"
fi
decidir escritorio "${D[inst_escritorio]:-}" "$PVER"
if [[ ${ACCION[escritorio]} == aldia && ${#FALTAN_ICONOS[@]} -gt 0 ]]; then
    ACCION[escritorio]=instalar; MOTIVO[escritorio]="faltan iconos"
fi
decidir manual "${D[inst_manual]:-}" "$PVER"
if [[ ${D[lanzador]:-} == ajeno && ${ACCION[programa]} == instalar ]] && ((!FORZAR)); then
    error "$P/bin/evoluciona existe y no es el lanzador de Evoluciona; usa -f para sustituirlo"
fi
generar_lanzadores

titulo "Plan (Evoluciona $PVER, commit $COMMIT)"
for c in programa escritorio manual; do printf '  %-11s %s\n' "$c:" "${MOTIVO[$c]}"; done
msg "  Detección de la figura: ${MODO^^} ($MOTIVO_GPU); el programa lo vuelve a comprobar en cada ejecución"

pendiente=0
for c in programa escritorio manual; do [[ ${ACCION[$c]} != instalar ]] || pendiente=1; done
if ((!pendiente)); then
    omitidos=0
    for c in programa escritorio manual; do [[ ${ACCION[$c]} != omitir ]] || omitidos=1; done
    if ((omitidos)); then
        msg $'\nNo se instala nada: hay componentes de la misma versión o superior (usa -f para forzarlo).'
    else
        msg $'\nTodo está al día.'
    fi
    exit 0
fi

existe() { local f; for f in "${EXISTEN[@]}"; do [[ $f == "$1" ]] && return 0; done; return 1; }
lista() { local f; for f in "$@"; do if existe "$f"; then msg "  sustituye  $f"; else msg "  crea       $f"; fi; done; }

titulo "Cambios en $DONDE"
if [[ ${ACCION[programa]} == instalar ]]; then
    msg "  crea       $P/lib/evoluciona/$PVER-$COMMIT-<fecha>/  (entorno de Python, ~$([[ $MODO == gpu ]] && echo '3,5 GB' || echo '700 MB'))"
    if [[ -n ${D[inst_programa]:-} ]]; then msg "  sustituye  $P/lib/evoluciona/actual  (enlace a la versión activa)"
    else msg "  crea       $P/lib/evoluciona/actual  (enlace a la versión activa)"; fi
    for v in "${ANTIGUAS[@]}"; do msg "  borra      $v"; done
    [[ -z ${D[inst_programa]:-} ]] || msg "  borra      la versión instalada ahora (${D[inst_programa]}), tras activar la nueva"
    lista "$P/bin/evoluciona"
fi
if [[ ${ACCION[escritorio]} == instalar ]]; then
    lista "$P/share/applications/evoluciona.desktop" "$P/share/icons/hicolor/scalable/apps/evoluciona.svg"
    for t in $TAMANOS_ICONO; do lista "$P/share/icons/hicolor/${t}x$t/apps/evoluciona.png"; done
fi
[[ ${ACCION[manual]} != instalar ]] || lista "$M/evoluciona.1"
msg "Los modelos (~170 MB) se descargan la primera vez que se usan, en ~/.cache/evoluciona/ de cada usuario."

if ((SIMULAR)); then msg $'\nSimulación: no se ha cambiado nada.'; exit 0; fi
confirmar $'\n'"¿Instalar en $DONDE?" || { msg "Cancelado."; exit "$E_CANCELADO"; }

# ------------------------------------------------------------------------------------- instalación
if [[ -n $EQUIPO ]]; then
    tar -C "$TMP/carga" -cf - . | remoto tar -xf - -C "$RT" || error "no se pudo copiar el paquete a $EQUIPO"
    CARGA="$RT"
else
    CARGA="$TMP/carga"
fi
componentes=""
for c in programa escritorio manual; do [[ ${ACCION[$c]} != instalar ]] || componentes+="${componentes:+,}$c"; done
destino instalar "$componentes" "$P" "$M" "$CARGA" "$PVER" "$COMMIT" "$MODO" || error "la instalación no terminó bien"

# ------------------------------------------------------------------------------------------ resumen
consultar_destino
titulo "Comprobación ($P/bin/evoluciona --diagnostico)"
destino diagnostico "$P" || aviso "el diagnóstico del programa instalado ha fallado"

titulo "Resumen"
nota() { msg "  Aviso: $*"; }  # avisos del resumen (los anteriores se repiten al final)
read -r v_prog c_prog m_prog <<< "${D[inst_programa]:-}"
msg "  programa:   ${v_prog:-?} (commit ${c_prog:-?}, ${m_prog:-?})"
msg "  escritorio: ${D[inst_escritorio]:-?}"
msg "  manual:     ${D[inst_manual]:-?}"
if [[ "${v_prog:-}|${v_prog:-}" != "${D[inst_escritorio]}|${D[inst_manual]}" ]]; then
    nota "quedan versiones mezcladas entre programa, lanzador y manual"
fi
[[ ${D[en_path]} == si ]] || nota "$P/bin no está en el PATH de $DONDE: lánzalo como $P/bin/evoluciona"
[[ ${D[en_manpath]} != no ]] || nota "$(dirname "$M") no está en el manpath de $DONDE: usa «man -l $M/evoluciona.1»"
[[ ${D[en_xdg]} == si ]] || nota "$P/share no está en XDG_DATA_DIRS: el menú de aplicaciones no verá el lanzador"
[[ ${D[wsl]} != si ]] || msg "  En WSL (WSLg), el lanzador suele aparecer en el menú Inicio de Windows como «Evoluciona (WSL)»."
if ((${#AVISOS[@]})); then
    msg "  Avisos anteriores:"; for a in "${AVISOS[@]}"; do msg "   - $a"; done
fi
msg "Listo."
