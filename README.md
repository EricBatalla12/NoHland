# lienzo

Gestor de fondos de pantalla para Hyprland + Noctalia, hecho a medida.

- **Imágenes** (png, jpg, webp, jxl, avif…): las dibuja Noctalia con su
  transición (`noctalia msg wallpaper-set`), que de paso regenera la paleta.
- **Vídeo (mp4, webm) y GIF**: mpvpaper en la capa `bottom` (encima del fondo
  de Noctalia y debajo de las ventanas), sin audio y con `hwdec=nvdec`.
- **Paleta**: con un vídeo, ffmpeg saca un fotograma (al 25 % de la duración)
  y Noctalia lo recibe como fondo de ese monitor, debajo de mpvpaper: de ahí
  salen los colores y lo que ve la pantalla de bloqueo. Los fotogramas se
  cachean en `~/.cache/lienzo/frames`.
- **Un fondo por monitor**, cada uno con su mpvpaper. La paleta la manda un
  monitor (`palette_monitor`). Al desconectar un monitor se para su vídeo; al
  volver a conectarlo recupera su fondo.
- **Pausa automática**, que se suma a la manual:
  - ventana a pantalla completa en el workspace visible de un monitor
    (pausa solo ese monitor);
  - ventana activa de Steam, un juego de Proton, Lutris o Heroic (lista
    configurable; pausa todos);
  - portátil con batería (pausa todos).

  Todo por eventos (socket2 de Hyprland y uevents del kernel por netlink):
  sin sondeos, 0 % de CPU en reposo.

## Uso

```
lienzo set <archivo>   # pone un fondo en todos los monitores
lienzo set -m DP-1 <archivo>   # ...o solo en uno
lienzo next [-m DP-1]  # siguiente de la carpeta de cada monitor (orden alfabético)
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
| `lienzo/backends.py` | Noctalia para imágenes, mpvpaper + IPC de mpv para vídeo, fotograma con ffmpeg |
| `lienzo/energia.py` | batería: lectura de `/sys/class/power_supply` y uevents del kernel |
| `lienzo/config.py` | rutas y lectura de `config.toml` |

Rutas en ejecución: socket de control en `$XDG_RUNTIME_DIR/lienzo/control.sock`,
último fondo de cada monitor en `~/.local/state/lienzo/state.json`.

**Cómo sale la paleta (comprobado):** Noctalia la genera desde el último
`noctalia msg wallpaper-set` que recibe, del monitor que sea y aunque repita la
ruta. lienzo deja siempre para el final el monitor de la paleta; si se cambia
otro monitor, le repite el suyo.
