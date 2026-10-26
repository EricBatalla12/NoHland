# NoHland

El nombre viene de **No**ctalia + **H**yprland: está hecho para ese escritorio.

Gestor de fondos de pantalla para Hyprland + Noctalia, hecho a medida.

- **Imágenes** (png, jpg, webp, jxl, avif…): las dibuja Noctalia con su
  transición (`noctalia msg wallpaper-set`), que de paso regenera la paleta.
- **Vídeo (mp4, webm) y GIF**: mpvpaper en la capa `bottom` (encima del fondo
  de Noctalia y debajo de las ventanas), sin audio y con decodificación por
  hardware (`auto-safe`, o `nvdec` fijo en NVIDIA).
- **Paleta**: con un vídeo, ffmpeg saca un fotograma (al 25 % de la duración)
  y Noctalia lo recibe como fondo de ese monitor, debajo de mpvpaper: de ahí
  salen los colores y lo que ve la pantalla de bloqueo. Los fotogramas se
  cachean en `~/.cache/nohland/frames`.
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
- **Un fondo por workspace**: cada workspace de Hyprland puede tener su fondo
  propio («Ponerlo solo en el workspace N» en el menú de una tarjeta, o
  `nohland set -w <archivo>`); al cambiar de workspace se pone el suyo y, en
  los que no tienen, el general del monitor. Elegir fondo, `next` o la rotación
  cambian el propio si el workspace visible lo tiene y el general si no.
  «Usar el fondo general» (en la portada) o `nohland workspace clear` lo quitan.
  «Ponerlo de fondo general» (menú de la tarjeta, cuando algún workspace
  visible tiene fondo propio) o `nohland set -g <archivo>` lo ponen como
  general y el workspace visible deja su fondo propio (los demás conservan el
  suyo).
  Cada cambio de workspace con fondo distinto recolorea el escritorio (la
  paleta sigue al fondo) y, si es un vídeo, lo empieza desde el principio.
- **Rotación automática** (interruptor en la ventana o `nohland rotate on`):
  cada N minutos cada monitor pasa a otro fondo de su carpeta, en orden o al
  azar, solo entre los que estén en la rotación. El tiempo, el orden y qué
  fondos entran se cambian en la ventana sin reiniciar nada y se guardan en
  `state.json` (`[rotation] interval_minutes` de config.toml es solo el
  tiempo de partida).
  Un monitor en pausa no cambia: queda pendiente y cambia cuando lleva 30 s sin
  pausa, para no recolorear el escritorio en mitad de un juego ni en un alt-tab.
  Elegir un fondo a mano reinicia la cuenta. Es un único temporizador, así que
  no cuesta nada entre cambios. Los fotogramas de todos los vídeos de la
  carpeta se quedan en caché: sacar el de un 4K a 60 fps cuesta ~4 s de CPU.
- **Pasar los fondos a otro equipo**: «Exportar fondos…» (menú de la ventana)
  o `nohland export <archivo.zip>` guardan en un .zip los fondos de las
  carpetas y lo elegido (fondo general, el de cada workspace y los que no
  entran en la rotación). En el otro equipo, «Importar fondos…» o
  `nohland import <archivo.zip>` copian los fondos a su carpeta y aplican esas
  elecciones; el fondo general va a todos sus monitores. Un fondo que ya está
  (mismo nombre y tamaño) no se duplica. **Los de Steam (Wallpaper Engine) no
  se pasan**: son enlaces al Workshop de cada cuenta; el otro equipo los tiene
  si está suscrito. El .zip va al otro equipo como quieras (USB, Syncthing…).

## Interfaz

`nohland gui`, o «NoHland» en el lanzador de Noctalia. Una ventana GTK4 +
libadwaita que hace todo lo del CLI:

- **Portada**: el fondo que suena, en grande, con su tipo, decodificador,
  estado y motivos de pausa, y los botones Pausar/Reanudar y Siguiente.
- **Colección**: tarjetas con miniatura de la carpeta (clic = ponerlo), filtro
  Todo / Imágenes / Animados, búsqueda (Ctrl+F) y botón para abrir la carpeta.
  El fondo en uso lleva la etiqueta «EN USO».
- **Varios monitores**: selector en la barra y una fila por monitor. Con un
  solo monitor no aparece.
- **Rotación**: interruptor junto a la colección y un panel con el tiempo
  (5 min, 15 min, 30 min, 1 h, 3 h o el que quieras), el orden (en orden o
  aleatorio) y «Elegir…», que pone la colección en modo selección: un clic
  mete o saca cada fondo de la rotación. La portada muestra «Cambia en X min».
- **Menú de cada tarjeta** (botón ⋮ al pasar el ratón, o clic derecho):
  ponerlo en todos los monitores o en uno, meterlo o sacarlo de la rotación,
  mostrarlo en la carpeta o moverlo a la papelera (pide confirmación).
- **Conseguir fondos**: botón con enlaces a Wallhaven, Wallpaper Abyss,
  MoeWalls, MotionBGs y el Workshop de Wallpaper Engine. Solo abre la web en el
  navegador; lo que descargues en la carpeta aparece solo en la colección.
- **Colores e inicio de sesión** (botón de la paleta en la barra):
  - «Los colores siguen al fondo»: enciende o apaga la paleta de Noctalia
    sacada del fondo (`noctalia msg color-scheme-set`), con el estilo del
    generador (fiel, suave, vivo...). Al apagarla vuelve a la paleta fija que
    tuvieras en Noctalia. «Elegir qué apps cambian de color…» abre los ajustes
    de Noctalia en Plantillas, con una casilla por aplicación (GTK, Qt, kitty,
    btop, bordes de Hyprland...).
  - Fondo de la pantalla de inicio de sesión: «Como el escritorio» (con
    vídeos, su fotograma), «Imagen fija» (también desde el menú de cada
    tarjeta: «Usar en el inicio de sesión») o «No tocarla». Ver más abajo.
- «Abrir…» para un archivo de otra carpeta, y en el menú, Ajustes (edita
  `config.toml` y reinicia el servicio) y Acerca de.

- Coge los colores de la paleta del fondo sola: Noctalia escribe los colores
  de libadwaita en `~/.config/gtk-4.0/noctalia.css`.
- Pide el estado cada 2 s solo mientras está abierta.
- Al guardar los ajustes se pierden los comentarios del `config.toml`, y el
  reinicio del servicio quita la pausa manual y empieza los vídeos de nuevo.
- Atajos: Ctrl+Espacio pausa/reanuda, Ctrl+→ siguiente, Ctrl+F busca, Ctrl+Q cierra.
- Cada botón es una acción de la aplicación, así que se puede manejar por
  D-Bus (así se prueba sin ratón):
  `gdbus call --session --dest io.github.EricBatalla12.NoHland --object-path /io/github/EricBatalla12/NoHland --method org.gtk.Actions.Activate siguiente "[]" "{}"`

## Uso desde la terminal

```
nohland set <archivo>   # pone un fondo en todos los monitores
nohland set -m DP-1 <archivo>   # ...o solo en uno
nohland next [-m DP-1]  # siguiente de la carpeta de cada monitor (orden alfabético)
nohland pause           # pausa manual
nohland resume          # quita la pausa manual (la automática sigue mandando)
nohland status          # qué suena, si está pausado y por qué (--json)
nohland set -w <archivo>        # solo en el workspace visible (fondo propio)
nohland set -g <archivo>        # fondo general; el workspace visible deja el suyo
nohland export fondos.zip       # los fondos y lo elegido, para otro equipo (sin los de Steam)
nohland import fondos.zip       # los trae a este equipo
nohland workspace clear # ese workspace vuelve al fondo general
nohland rotate on|off   # rotación automática
nohland rotate every 30 # cada 30 minutos
nohland rotate order aleatorio   # o «orden»
nohland rotate exclude <archivo> # sacarlo de la rotación (include lo vuelve a meter)
nohland greeter escritorio      # fondo del inicio de sesión = el del escritorio
nohland greeter fija <archivo>  # ...o uno concreto (no: no tocarlo)
nohland doctor          # revisa que el equipo tenga lo necesario
nohland gui             # la ventana
```

Logs: `journalctl --user -u nohland -f`

## Instalación

### Requisitos

NoHland está hecho para **Hyprland + Noctalia** y no funciona sin ellos.

| Qué | Para qué | Paquete (CachyOS / Arch) |
|---|---|---|
| Hyprland | el escritorio; NoHland escucha sus eventos | `hyprland` |
| Noctalia 5 (la nativa, no la de Quickshell) | dibuja las imágenes y saca la paleta | `noctalia` (Arch: AUR) |
| mpvpaper | fondos animados | `mpvpaper` (Arch: AUR) |
| ffmpeg | miniaturas y fotograma de los vídeos | `ffmpeg` |
| Python 3.11+ | todo NoHland (solo biblioteca estándar) | `python` |
| GTK 4.12+, libadwaita 1.7+ y PyGObject | solo la ventana (`nohland gui`) | `gtk4 libadwaita python-gobject` |

En Noctalia, el fondo tiene que estar activado (`[wallpaper] enabled = true`,
lo normal) y, para que los colores sigan al fondo, `[theme] source = "wallpaper"`.

Funciona con cualquier gráfica: la decodificación por defecto es `auto-safe`,
que mpv resuelve sola (en NVIDIA acaba en nvdec). Con NVIDIA se puede fijar
`nvdec` en Ajustes, que en las pruebas fue lo que menos CPU gastó.

### Pasos

```
git clone https://github.com/EricBatalla12/NoHland ~/Proyectos/NoHland
cd ~/Proyectos/NoHland
./install.sh
```

Después abre NoHland (lanzador o `nohland gui`) y pulsa **«Arrancar»** en el
aviso de arriba: lo arranca y lo deja habilitado para que arranque solo en cada
inicio de sesión. Es lo mismo que `systemctl --user enable --now nohland`.

`install.sh` no instala paquetes ni pide contraseña. Primero comprueba los
requisitos y, si falta algo imprescindible, dice qué y cómo conseguirlo, sin
tocar nada (`--forzar` lo instala igualmente). Después enlaza `bin/nohland` en
`~/.local/bin`, copia el servicio a `~/.config/systemd/user`, enlaza el icono en el tema
hicolor, crea la entrada del lanzador, copia `config.example.toml` a
`~/.config/nohland/config.toml` si no hay ninguna y termina con
`nohland doctor`.

La carpeta de fondos por defecto es `NoHland` dentro de la carpeta de imágenes
del sistema (`~/Imágenes/NoHland`, `~/Pictures/NoHland`... según el idioma). Se
crea sola al arrancar.

Para actualizar no hace falta la terminal: al abrir la ventana, NoHland mira
en GitHub (como mucho cada 6 horas) si hay versión nueva y, si la hay, avisa
arriba con «Ver y actualizar». El diálogo enseña las novedades (los títulos de
los commits nuevos) y «Actualizar» hace `git pull --ff-only`, vuelve a pasar
`install.sh` y reinicia el servicio. Nunca actualiza solo. Si la copia tiene
cambios sin guardar, avisa pero no la toca; si va por delante de GitHub (el
equipo donde se desarrolla), no avisa. También: menú → «Buscar
actualizaciones», y se puede apagar en Ajustes (`check_updates = false`).

A mano sería: `git pull`, `./install.sh` y `systemctl --user restart nohland`.

### Comprobar el equipo

```
nohland doctor
```

Revisa Hyprland, Noctalia (versión, que esté en marcha, que su fondo esté
activado y de dónde saca la paleta), mpvpaper, ffmpeg, la decodificación, la
interfaz, el servicio y la carpeta de fondos, y dice cómo arreglar lo que
falle. No cambia nada.

### Sin UWSM

El servicio arranca con `graphical-session.target`, que levanta UWSM con el
entorno de Hyprland ya cargado. Si inicias Hyprland sin UWSM, ese target no se
activa y systemd no conoce la sesión (`nohland doctor` lo detecta). En ese
caso, arráncalo desde el autostart de Hyprland:

```
# hyprland.conf
exec-once = dbus-update-activation-environment --systemd --all && systemctl --user start nohland
```

```lua
-- config de Hyprland en Lua
hl.exec_cmd("dbus-update-activation-environment --systemd --all && systemctl --user start nohland")
```

**Por qué un servicio systemd y no `exec-once` a secas:** systemd reinicia el
daemon si cae y manda los logs al journal sin hacer nada más.

## Pantalla de inicio de sesión (opcional)

El greeter (noctalia-greeter) corre como otro usuario: no puede leer tu home y
su carpeta es de root. Para que NoHland le cambie el fondo sin pedir
contraseña hay que prepararlo **una vez**:

```
sudo ./tools/preparar-greeter.sh
```

Crea `/var/lib/greeter-wallpaper` (tuya para escribir, legible por el greeter)
y añade a `/var/lib/noctalia-greeter/greeter.toml` que el fondo es
`current.png` de esa carpeta (con copia de seguridad del fichero). A partir de
ahí NoHland copia la imagen en cada cambio: un fichero, sin contraseña y sin
coste apreciable (40 ms la orden completa, medido).

Con `--colores`, además, activa la sincronización oficial sin contraseña
(`noctalia-greeter passwordless-sync enable`). Después, en Noctalia:
Ajustes → Seguridad → Noctalia Greeter → «Auto-Sync Greeter», y los colores del
inicio de sesión seguirán a los del escritorio. El fondo lo sigue poniendo
NoHland: lo de greeter.toml gana a la sincronización.

## Icono

`data/io.github.EricBatalla12.NoHland.svg`: un paisaje nocturno (la noche de
Noctalia) dentro de un marco con el degradado cian-verde de los bordes de
Hyprland. `install.sh` lo enlaza en `~/.local/share/icons/hicolor`.

## Estructura

| Fichero | Qué hace |
|---|---|
| `nohland/cli.py` | subcomandos |
| `nohland/cliente.py` | cliente del socket de control (CLI e interfaz) |
| `nohland/gui.py` | ventana principal (GTK4 + libadwaita) |
| `nohland/ajustes.py` | ventana de ajustes |
| `nohland/miniaturas.py` | miniaturas con ffmpeg, cacheadas en `~/.cache/nohland/miniaturas` |
| `nohland/daemon.py` | bucle asyncio: socket de control + eventos de Hyprland + pausa |
| `nohland/hyprland.py` | cliente de `.socket.sock` (consultas) y `.socket2.sock` (eventos) |
| `nohland/backends.py` | Noctalia para imágenes, mpvpaper + IPC de mpv para vídeo, fotograma con ffmpeg |
| `nohland/energia.py` | batería: lectura de `/sys/class/power_supply` y uevents del kernel |
| `nohland/config.py` | rutas y lectura de `config.toml` |
| `nohland/paquete.py` | exportar e importar los fondos en un .zip para otro equipo |

Rutas en ejecución: socket de control en `$XDG_RUNTIME_DIR/nohland/control.sock`,
último fondo de cada monitor en `~/.local/state/nohland/state.json`.

**Cómo sale la paleta (comprobado):** Noctalia la genera desde el último
`noctalia msg wallpaper-set` que recibe, del monitor que sea y aunque repita la
ruta. NoHland deja siempre para el final el monitor de la paleta; si se cambia
otro monitor, le repite el suyo.
