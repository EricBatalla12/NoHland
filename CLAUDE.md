# CLAUDE.md — NoHland

Gestor de fondos de pantalla para **Hyprland + Noctalia 5** (Noctalia + Hyprland
= NoHland). Python 3.11+ solo con la biblioteca estándar; la ventana usa GTK4 +
libadwaita (PyGObject). Repo público: github.com/EricBatalla12/NoHland.

Lo de un equipo concreto (rutas, monitores, preferencias de trabajo) va en
`CLAUDE.local.md`, que no se sube.

## Cómo está hecho

| Pieza | Fichero | Qué hace |
|---|---|---|
| Daemon | `nohland/daemon.py` | Bucle asyncio sin sondeos: socket de control, socket2 de Hyprland, uevents del kernel (batería), temporizador de rotación. Estado por monitor, pausa, rotación, greeter |
| CLI | `nohland/cli.py` + `cliente.py` | Subcomandos; todo salvo `daemon`, `gui` y `doctor` habla con el daemon por `$XDG_RUNTIME_DIR/nohland/control.sock` (JSON por línea) |
| Ventana | `nohland/gui.py`, `ajustes.py` | Otro cliente del socket. Cada botón es una acción de la app (`app.siguiente`, `app.poner`…), así que se puede manejar por D-Bus |
| Reproducción | `nohland/backends.py` | Imágenes → `noctalia msg wallpaper-set [monitor] ruta`. Vídeo/GIF → un `mpvpaper` por monitor en la capa `bottom`, controlado por el IPC de mpv. Fotograma de vídeos con ffmpeg para la paleta |
| Otros | `hyprland.py`, `energia.py`, `greeter.py`, `miniaturas.py`, `actualizar.py`, `doctor.py`, `config.py` | Sockets de Hyprland; batería por netlink; fondo del greeter; miniaturas; buscar/instalar versiones; revisión del equipo; config |

Rutas en ejecución: config `~/.config/nohland/config.toml` (lo editable a mano y
desde Ajustes), estado `~/.local/state/nohland/state.json` (fondo por monitor,
rotación, greeter: lo que se cambia desde la ventana sin reiniciar), caché
`~/.cache/nohland/` (fotogramas, miniaturas, comprobación de versiones).
Servicio: `systemd/nohland.service`, que `install.sh` **copia** (no enlaza).

## Cosas comprobadas que no hay que volver a descubrir

- **Paleta de Noctalia:** sale del ÚLTIMO `wallpaper-set` que recibe, del monitor
  que sea y aunque repita la ruta. Por eso el monitor de la paleta se pone el
  último y, si cambia otro, se le repite el suyo (`_aplicar`).
- **ffmpeg 9 + JPEG:** con `-ss` sobre una imagen fija no saca fotograma y aun así
  sale con código 0. `-ss` solo para vídeos; usar `-update 1`.
- **mpvpaper** recibe las opciones de mpv en una cadena y la parte por espacios:
  la config rechaza opciones con espacios.
- **`hwdec=auto-safe`** con la salida OpenGL de mpvpaper descarta vulkan y en
  NVIDIA acaba en nvdec. `nvdec` fijo es lo que menos CPU gasta en NVIDIA.
- **mpvpaper en pausa** sigue gastando ~2 % de un núcleo (su bucle de render).
- **asyncio:** guardar el `StreamWriter` de socket2 aunque no se use; si se
  descarta, el recolector cierra el socket y `readline()` devuelve EOF.
- **IPC de mpv:** cerrar con `write_eof()` y leer hasta el final; cortar con
  eventos sin leer provoca `Read error (Connection reset by peer)` en mpv.
- **systemd:** con el servicio instalado como enlace, `systemctl --user disable`
  borra el enlace. Por eso se copia.
- **Gtk.Picture** pide el tamaño natural de la imagen y estira lo que la
  contiene: para imágenes recortadas está `Cubierta` (widget propio).
- **Python 3.14 / zsh:** en los scripts de prueba no usar `md` como nombre de
  función (es alias de mkdir en el shell que usa la herramienta) ni f-strings
  con comillas escapadas dentro de `python3 -c '…'`.
- Noctalia: `noctalia msg color-scheme-get|set`, `settings-open templates`
  (abre Plantillas), hooks en `[hooks]` (`wallpaper_changed` con
  `NOCTALIA_WALLPAPER_PATH`, `colors_changed`).
- Greeter: `passwordless-sync` oficial de noctalia-greeter ≥ 1.5; el fondo propio
  va en `greeter.toml` (gana a la sincronización); ver `tools/preparar-greeter.sh`.

## Convenciones

- Todo en español: interfaz, logs, comentarios, commits, README. En los
  comentarios del código no se ponen tildes; en lo que ve el usuario, sí.
- Los comentarios explican el porqué de lo no obvio, como el resto del código.
- Commits: título corto en forma de frase («La rotación se configura desde la
  ventana…»), cuerpo con viñetas, y la línea `Co-Authored-By` que toque.
  Autor con el correo noreply de GitHub (nunca un correo personal: el repo es
  público).
- Alcance pequeño: las ideas que salgan van a `TODO.md`, no al código.
- `README.md` documenta cada función nueva; `config.example.toml` cada clave.

## Cómo probar sin estropear el escritorio de quien lo usa

Se prueba en el equipo real, mientras la persona lo está usando:

- Medir antes de afirmar (CPU con `/proc/PID/stat`, GPU con `nvidia-smi pmon`).
- Lógica del daemon: en un proceso aislado, creando `daemon.Daemon(cfg)` con
  `_aplicar` sustituido por uno que solo apunte, y `Guardado.escribir`
  desactivado para no tocar el `state.json` real.
- Ventana: lanzar una copia con otro id (`gui.APP_ID = "…NoHlandPrueba"`) para no
  secuestrar la ventana abierta del usuario (la app es de instancia única), y
  manejarla con sus acciones. Capturas con `grim` solo en un workspace que esté
  vacío justo antes y con el workspace especial cerrado.
- **Nunca** `hyprctl dispatch 'hl.dsp.window.close()'`: actúa sobre la ventana
  activa, que puede ser cualquiera. Cerrar por la acción `app.salir` o por PID.
- Instalación y actualizaciones: con un `HOME` falso y una copia del repo en el
  scratchpad, para no tocar la instalación real.
- Si una prueba cambia el fondo, la pausa, la paleta o el servicio del usuario,
  dejarlos como estaban y decirlo.
