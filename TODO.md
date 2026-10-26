# TODO

Ideas apuntadas para no ampliar el alcance. Nada de esto está hecho.
Cada una lleva lo que aporta y lo que costaría (poco / medio / mucho).

## Recomendadas para la próxima tanda

1. **Fondos de día y de noche.** Dos listas, o etiquetas en cada fondo, y
   cambio a la hora que digas (o al cambiar Noctalia entre claro y oscuro con
   el hook `theme_mode_changed`). Coste: medio.
2. **Importar desde Wallpaper Engine.** Ver tus suscripciones de tipo Vídeo
   con su miniatura y enlazarlas a la carpeta con un clic, sin terminal.
   Coste: medio.
3. **Optimizar vídeos pesados.** Convertir un 4K a 60 fps a la resolución del
   monitor y 30 fps con ffmpeg (NVENC), guardando el original. Estimación por
   lo medido (4K60 ~8 % de un núcleo, 1080p30 ~5 %): ahorraría unos 3 puntos
   por vídeo 4K. Coste: medio.
4. **Detalles de cada fondo**: resolución, duración, peso y códec (ffprobe) en
   el menú de la tarjeta, y ordenar la colección por nombre, fecha o tipo.
   Coste: poco.

## Colección e interfaz
- Favoritos y listas propias (p. ej. «One Piece», «tranquilos») para usarlas
  en la rotación.
- Buscar y descargar de Wallhaven desde la propia ventana (su API, solo SFW),
  sin pasar por el navegador.
- Vista previa animada de los vídeos en la cuadrícula al pasar el ratón.
- Widget en la barra de Noctalia (plugin en Luau) con siguiente y pausa.
- Regla de Hyprland para que la ventana salga flotante y centrada.
- Lista de webs de «Conseguir fondos» editable desde config.toml.
- Encuadre por fondo: qué parte se ve al recortar (arriba, centro, abajo).
- Avisar de duplicados al añadir.

## Reproducción y rendimiento
- **Parar en vez de pausar.** mpvpaper en pausa sigue gastando ~2,3 % de un
  núcleo (medido: 23 ticks/10 s con nvdec, vaapi y software por igual; es su
  bucle de render, no el decodificador). Con un juego delante se podría cerrar
  mpvpaper y relanzarlo en la misma posición (`start=<time-pos>`), dejando el
  frame de Noctalia debajo. Con batería, en el portátil, ese ~2 % sí cuenta.
- Limitar los FPS de los vídeos con batería o en modo ahorro (hook
  `power_profile_changed` de Noctalia).
- Pausar con la pantalla bloqueada o apagada (DPMS).
- Velocidad de reproducción por fondo (ya se puede a mano con mpv_options).
- `gamescope` en la lista de clases por defecto (tu windowrules.lua ya lo trata
  como juego).
- Opción para pausar también con ventanas maximizadas (`fullscreen` modo 1).
- Pausa manual por monitor (`nohland pause -m`). Hoy la manual es global.

## Rotación
- Barajar sin repetir hasta haber pasado por todos (el aleatorio de ahora
  solo evita repetir el que está puesto).
- Rotación distinta por monitor (tiempo y fondos propios).

## Integración con el escritorio
- Escenas de Wallpaper Engine (linux-wallpaperengine) como tercer backend: hoy
  solo sirven los fondos de tipo Vídeo del Workshop.
- El selector de fondos de Noctalia (SUPER+SHIFT+W) cambia el fondo de Noctalia
  por debajo de NoHland sin que NoHland se entere.
- Elegir el esquema de color de la paleta (m3-content, vibrant...) por fondo, o
  congelar la paleta para que no cambie con el fondo.
- Notificación opcional al cambiar de fondo.

## Mantenimiento
- Recargar `config.toml` sin reiniciar el servicio (SIGHUP o `nohland
  reload`): hoy guardar los ajustes quita la pausa manual y reinicia los vídeos.
- Pasar las pruebas aisladas que se han hecho a mano (rotación, batería,
  ajustes) a una batería de tests en el repo.
- PKGBUILD para instalarlo con paru en vez de con install.sh.
