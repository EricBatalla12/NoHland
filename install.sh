#!/usr/bin/env bash
# Instala NoHland para el usuario actual (sin root):
#   - comprueba antes que esten Hyprland, Noctalia y los programas que usa
#   - enlaza el ejecutable en ~/.local/bin
#   - enlaza el servicio en ~/.config/systemd/user
#   - crea la entrada del lanzador (nohland gui) e instala su icono
#   - copia la config de ejemplo si todavia no hay ninguna
#   - termina con `nohland doctor`, que dice si falta algo mas
# Son enlaces al repo, asi que un `git pull` basta para actualizar.
#
#   ./install.sh            instala (se para si falta algo imprescindible)
#   ./install.sh --forzar   instala aunque falte algo
set -euo pipefail

repo="$(dirname "$(readlink -f "$0")")"
forzar=0
[ "${1:-}" = "--forzar" ] && forzar=1

# ------------------------------------------------------------ requisitos
# No se instala nada con pacman: solo se dice que falta y como conseguirlo.
faltan=()
falta() { faltan+=("$1  →  $2"); }

command -v hyprctl  >/dev/null || falta "Hyprland" "NoHland solo funciona sobre Hyprland"
command -v noctalia >/dev/null || falta "Noctalia 5" "CachyOS: sudo pacman -S noctalia · Arch: desde el AUR (paru)"
command -v mpvpaper >/dev/null || falta "mpvpaper" "CachyOS: sudo pacman -S mpvpaper · Arch: desde el AUR (paru)"
command -v ffmpeg   >/dev/null || falta "ffmpeg" "sudo pacman -S ffmpeg"
command -v python3  >/dev/null || falta "python 3.11+" "sudo pacman -S python"
if command -v python3 >/dev/null && ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    falta "python 3.11+" "tienes $(python3 -V 2>&1); actualiza python"
fi

# La interfaz es opcional: sin ella funcionan el daemon y el CLI
if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw' 2>/dev/null; then
    echo "aviso: sin GTK 4 / libadwaita para Python no habra ventana (nohland gui)."
    echo "       sudo pacman -S python-gobject gtk4 libadwaita"
fi

if [ ${#faltan[@]} -gt 0 ]; then
    echo "Falta lo necesario para NoHland:"
    printf '  - %s\n' "${faltan[@]}"
    if [ $forzar -eq 0 ]; then
        echo "No he instalado nada. Instalalo y vuelve a ejecutar ./install.sh (o usa --forzar)."
        exit 1
    fi
    echo "--forzar: sigo de todas formas."
fi

# ------------------------------------------------------------ instalacion
mkdir -p ~/.local/bin ~/.config/systemd/user ~/.config/nohland
ln -sfn "$repo/bin/nohland" ~/.local/bin/nohland
ln -sfn "$repo/systemd/nohland.service" ~/.config/systemd/user/nohland.service

# Icono de la app en el tema hicolor del usuario
mkdir -p ~/.local/share/icons/hicolor/scalable/apps
ln -sfn "$repo/data/io.github.EricBatalla12.NoHland.svg" \
    ~/.local/share/icons/hicolor/scalable/apps/io.github.EricBatalla12.NoHland.svg

# Entrada del lanzador. Se genera (no se enlaza) para poner la ruta absoluta.
mkdir -p ~/.local/share/applications
sed "s|@NOHLAND@|$HOME/.local/bin/nohland|" "$repo/data/nohland.desktop.in" \
    > ~/.local/share/applications/io.github.EricBatalla12.NoHland.desktop

if [ ! -e ~/.config/nohland/config.toml ]; then
    cp "$repo/config.example.toml" ~/.config/nohland/config.toml
    echo "config de ejemplo copiada a ~/.config/nohland/config.toml"
fi

systemctl --user daemon-reload 2>/dev/null || true

echo
echo "Instalado. Para arrancarlo con la sesion:"
echo "  systemctl --user enable --now nohland"
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "aviso: ~/.local/bin no esta en tu PATH; añadelo para usar el comando nohland." ;;
esac
echo
echo "Revision del equipo (nohland doctor):"
"$repo/bin/nohland" doctor || true
