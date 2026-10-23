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

## Interfaz

`lienzo gui`, o «lienzo» en el lanzador de Noctalia. Una ventana GTK4 +
libadwaita que hace todo lo del CLI: cuadrícula con miniaturas de la carpeta
(clic = ponerlo), «Abrir…» para un archivo de otro sitio, selector de monitor,
Pausar/Reanudar, Siguiente, estado de cada monitor con los motivos de pausa y
una ventana de ajustes que edita `config.toml` y reinicia el servicio.

- Coge los colores de la paleta del fondo sola: Noctalia escribe los colores
  de libadwaita en `~/.config/gtk-4.0/noctalia.css`.
- Pide el estado cada 2 s solo mientras está abierta.
- Al guardar los ajustes se pierden los comentarios del `config.toml`, y el
  reinicio del servicio quita la pausa manual y empieza los vídeos de nuevo.
- Atajos: Espacio pausa/reanuda, Ctrl+→ siguiente, Ctrl+Q cierra.
- Cada botón es una acción de la aplicación, así que se puede manejar por
  D-Bus (así se prueba sin ratón):
  `gdbus call --session --dest io.github.EricBatalla12.Lienzo --object-path /io/github/EricBatalla12/Lienzo --method org.gtk.Actions.Activate siguiente "[]" "{}"`

## Uso desde la terminal

```
lienzo set <archivo>   # pone un fondo en todos los monitores
lienzo set -m DP-1 <archivo>   # ...o solo en uno
lienzo next [-m DP-1]  # siguiente de la carpeta de cada monitor (orden alfabético)
lienzo pause           # pausa manual
lienzo resume          # quita la pausa manual (la automática sigue mandando)
lienzo status          # qué suena, si está pausado y por qué (--json)
lienzo gui             # la ventana
```

Logs: `journalctl --user -u lienzo -f`

## Instalación

```
./install.sh
systemctl --user enable --now lienzo
```

`install.sh` enlaza `bin/lienzo` en `~/.local/bin`, el servicio en
`~/.config/systemd/user`, crea la entrada del lanzador y copia `config.example.toml` a
`~/.config/lienzo/config.toml` si no existe. Solo usa la biblioteca estándar
de Python (3.11+ por `tomllib`); en el sistema hacen falta `mpvpaper` y
`noctalia`, y para la interfaz `python-gobject`, `gtk4` y `libadwaita`.

**Por qué un servicio systemd y no `exec-once`:** la sesión va con UWSM, así
que `graphical-session.target` ya trae el entorno de Hyprland; systemd además
reinicia el daemon si cae y manda los logs al journal sin hacer nada más.

## Estructura

| Fichero | Qué hace |
|---|---|
| `lienzo/cli.py` | subcomandos |
| `lienzo/cliente.py` | cliente del socket de control (CLI e interfaz) |
| `lienzo/gui.py` | ventana principal (GTK4 + libadwaita) |
| `lienzo/ajustes.py` | ventana de ajustes |
| `lienzo/miniaturas.py` | miniaturas con ffmpeg, cacheadas en `~/.cache/lienzo/miniaturas` |
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
