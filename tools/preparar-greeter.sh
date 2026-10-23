#!/usr/bin/env bash
# Prepara la pantalla de inicio de sesion (noctalia-greeter) para que NoHland
# pueda cambiarle el fondo SIN pedir contraseña. Se ejecuta una sola vez:
#
#   sudo ./tools/preparar-greeter.sh             fondo
#   sudo ./tools/preparar-greeter.sh --colores   fondo + colores sin contraseña
#
# El problema: el greeter corre como el usuario «greeter», no puede leer tu home
# (normalmente 700) y su carpeta, /var/lib/noctalia-greeter, es de root.
# La solucion es una carpeta intermedia, /var/lib/greeter-wallpaper: tuya para
# escribir y legible por el greeter. NoHland copia ahi current.png en cada
# cambio y el greeter la lee porque se lo dice greeter.toml.
#
# Se usa greeter.toml y no sync.toml porque greeter.toml gana siempre: la
# sincronizacion oficial de Noctalia reescribe sync.toml, pero nunca greeter.toml
# (docs de noctalia-greeter, docs/user/sync.md).
#
# Con --colores, ademas, activa la sincronizacion oficial sin contraseña
# (`noctalia-greeter passwordless-sync enable`, regla de polkit limitada a esa
# accion). Despues, en Noctalia: Ajustes → Seguridad → Noctalia Greeter →
# «Auto-Sync Greeter», y los colores del login seguiran al escritorio.
#
# Es idempotente y hace copia de greeter.toml antes de tocarlo.
# Prueba sin root sobre un fichero cualquiera:  ./preparar-greeter.sh --prueba fichero.toml

set -euo pipefail

CARPETA="/var/lib/greeter-wallpaper"
FONDO="$CARPETA/current.png"
TOML="/var/lib/noctalia-greeter/greeter.toml"
colores=0
prueba=""

while [ $# -gt 0 ]; do
    case "$1" in
        --colores) colores=1 ;;
        --prueba)  prueba="$2"; shift ;;
        *) echo "uso: sudo $0 [--colores]" >&2; exit 1 ;;
    esac
    shift
done

# ------------------------------------------------------------ greeter.toml
# Quita las secciones [appearance.wallpaper] y [appearance.wallpapers.*] que
# hubiera y añade las de NoHland: la general y una por cada monitor que conozca
# el greeter (las de un monitor concreto ganarian a la general).
reescribir_toml() {
    python3 - "$1" "$FONDO" "${2:-}" <<'PY'
import re, sys
ruta, fondo, conectores = sys.argv[1], sys.argv[2], [c for c in sys.argv[3].split() if c]
try:
    texto = open(ruta, encoding="utf-8").read()
except FileNotFoundError:
    texto = ""
salida, saltando = [], False
for linea in texto.splitlines():
    cabecera = re.match(r"\s*\[([^\]]+)\]\s*$", linea)
    if cabecera:
        nombre = cabecera.group(1).strip()
        saltando = nombre == "appearance.wallpaper" or nombre.startswith("appearance.wallpapers.")
    if not saltando and "puesto por NoHland" not in linea:
        salida.append(linea)
while salida and not salida[-1].strip():
    salida.pop()
bloque = ["", "# Fondo puesto por NoHland (tools/preparar-greeter.sh): lo actualiza en cada cambio",
          "[appearance.wallpaper]", f'path = "{fondo}"']
for c in conectores:
    bloque += ["", f"[appearance.wallpapers.{c}]", f'path = "{fondo}"']
open(ruta, "w", encoding="utf-8").write("\n".join(salida + bloque) + "\n")
print(f"   secciones de fondo: general + {len(conectores)} monitor(es) {' '.join(conectores)}")
PY
}

if [ -n "$prueba" ]; then
    reescribir_toml "$prueba" "${NOHLAND_CONECTORES:-HDMI-A-1}"
    exit 0
fi

[ "${EUID:-$(id -u)}" -eq 0 ] || { echo "error: ejecutalo con sudo" >&2; exit 1; }
command -v noctalia-greeter >/dev/null || { echo "error: noctalia-greeter no esta instalado" >&2; exit 1; }
[ -d "$(dirname "$TOML")" ] || { echo "error: no existe $(dirname "$TOML")" >&2; exit 1; }

U="${SUDO_USER:-}"
[ -n "$U" ] && [ "$U" != root ] || { echo "error: lanzalo con sudo desde tu usuario, no como root" >&2; exit 1; }

echo "== 1. carpeta compartida: $CARPETA"
install -d -o "$U" -g "$(id -gn "$U")" -m 0755 "$CARPETA"
stat -c '   %A %U:%G %n' "$CARPETA"

echo "== 2. greeter.toml: leer el fondo de $FONDO"
[ -f "$TOML" ] && cp -a "$TOML" "$TOML.bak.$(date +%Y%m%d%H%M%S)" && echo "   copia: $TOML.bak.*"
CONECTORES="$(noctalia-greeter outputs 2>/dev/null | awk '{print $1}' | grep -E '^[A-Za-z]+-' | tr '\n' ' ' || true)"
reescribir_toml "$TOML" "$CONECTORES"
chown greeter:greeter "$TOML" 2>/dev/null || true
chmod 0644 "$TOML"

if [ $colores -eq 1 ]; then
    echo "== 3. colores: sincronizacion oficial sin contraseña para $U"
    noctalia-greeter passwordless-sync enable "$U"
    echo "   Ahora en Noctalia: Ajustes → Seguridad → Noctalia Greeter → «Auto-Sync Greeter»."
fi

echo
echo "Listo. En NoHland: botón de colores e inicio de sesión → «Como el escritorio» o «Imagen fija»."
echo "Se vera la proxima vez que salga la pantalla de inicio de sesion."
