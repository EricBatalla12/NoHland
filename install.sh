#!/usr/bin/env bash
# Instala NoHland para el usuario actual (sin root):
#   - enlaza el ejecutable en ~/.local/bin
#   - enlaza el servicio en ~/.config/systemd/user
#   - crea la entrada del lanzador (nohland gui) e instala su icono
#   - copia la config de ejemplo si todavia no hay ninguna
# Son enlaces al repo, asi que un `git pull` basta para actualizar.
set -euo pipefail

repo="$(dirname "$(readlink -f "$0")")"

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

systemctl --user daemon-reload
echo "listo. Para arrancarlo con la sesion: systemctl --user enable --now nohland"
