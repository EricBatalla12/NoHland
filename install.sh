#!/usr/bin/env bash
# Instala lienzo para el usuario actual (sin root):
#   - enlaza el ejecutable en ~/.local/bin
#   - enlaza el servicio en ~/.config/systemd/user
#   - crea la entrada del lanzador (lienzo gui)
#   - copia la config de ejemplo si todavia no hay ninguna
# Son enlaces al repo, asi que un `git pull` basta para actualizar.
set -euo pipefail

repo="$(dirname "$(readlink -f "$0")")"

mkdir -p ~/.local/bin ~/.config/systemd/user ~/.config/lienzo
ln -sfn "$repo/bin/lienzo" ~/.local/bin/lienzo
ln -sfn "$repo/systemd/lienzo.service" ~/.config/systemd/user/lienzo.service

# Entrada del lanzador. Se genera (no se enlaza) para poner la ruta absoluta.
mkdir -p ~/.local/share/applications
sed "s|@LIENZO@|$HOME/.local/bin/lienzo|" "$repo/data/lienzo.desktop.in" \
    > ~/.local/share/applications/io.github.EricBatalla12.Lienzo.desktop

if [ ! -e ~/.config/lienzo/config.toml ]; then
    cp "$repo/config.example.toml" ~/.config/lienzo/config.toml
    echo "config de ejemplo copiada a ~/.config/lienzo/config.toml"
fi

systemctl --user daemon-reload
echo "listo. Para arrancarlo con la sesion: systemctl --user enable --now lienzo"
