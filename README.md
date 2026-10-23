# lienzo

Gestor de fondos de pantalla para Hyprland + Noctalia, hecho a medida.

- **Imágenes** (png, jpg, webp, jxl, avif…): las dibuja Noctalia con su
  transición (`noctalia msg wallpaper-set`), que de paso regenera la paleta.
- **Vídeo (mp4, webm) y GIF**: mpvpaper en la capa `bottom` (encima del fondo
  de Noctalia y debajo de las ventanas), sin audio y con `hwdec=nvdec`.
- **Pausa automática** cuando hay una ventana a pantalla completa en un
  workspace visible o cuando la ventana activa es de Steam, un juego de
  Proton, Lutris o Heroic (lista configurable). Escucha el socket2 de Hyprland:
  sin sondeos, 0 % de CPU en reposo.

## Uso

```
lienzo set <archivo>   # pone un fondo
lienzo next            # siguiente de la carpeta (orden alfabético)
lienzo pause           # pausa manual
lienzo resume          # quita la pausa manual (la automática sigue mandando)
lienzo status          # qué suena, si está pausado y por qué (--json)
```

Logs: `journalctl --user -u lienzo -f`

## Instalación

```
./install.sh
systemctl --user enable --now lienzo
```

`install.sh` enlaza `bin/lienzo` en `~/.local/bin`, el servicio en
`~/.config/systemd/user` y copia `config.example.toml` a
`~/.config/lienzo/config.toml` si no existe. Solo usa la biblioteca estándar
de Python (3.11+ por `tomllib`); en el sistema hacen falta `mpvpaper` y
`noctalia`.

**Por qué un servicio systemd y no `exec-once`:** la sesión va con UWSM, así
que `graphical-session.target` ya trae el entorno de Hyprland; systemd además
reinicia el daemon si cae y manda los logs al journal sin hacer nada más.

## Estructura

| Fichero | Qué hace |
|---|---|
| `lienzo/cli.py` | subcomandos; cliente del socket de control |
| `lienzo/daemon.py` | bucle asyncio: socket de control + eventos de Hyprland + pausa |
| `lienzo/hyprland.py` | cliente de `.socket.sock` (consultas) y `.socket2.sock` (eventos) |
| `lienzo/backends.py` | Noctalia para imágenes, mpvpaper + IPC de mpv para vídeo |
| `lienzo/config.py` | rutas y lectura de `config.toml` |

Rutas en ejecución: socket de control en `$XDG_RUNTIME_DIR/lienzo/control.sock`,
último fondo en `~/.local/state/lienzo/state.json`.
