# TODO

Ideas apuntadas para no ampliar el alcance. Nada de esto está hecho.

## Interfaz
- Vista previa animada de los vídeos en la cuadrícula (al pasar el ratón).
- Importar desde Wallpaper Engine: elegir entre tus suscripciones de tipo
  Vídeo y crear el enlace en la carpeta sin tocar la terminal.
- Lista de webs de «Conseguir fondos» editable desde config.toml.
- Rotación en orden aleatorio, no solo alfabético.
- Que guardar los ajustes no reinicie el servicio (ver «Recargar config.toml»):
  hoy cambiar el intervalo quita la pausa manual y reinicia los vídeos.
- Widget en la barra de Noctalia (plugin en Luau) que abra la ventana.
- Regla de Hyprland para que la ventana salga flotante y centrada.
- Recargar la config sin reiniciar el servicio, para que guardar los ajustes
  no quite la pausa manual ni reinicie los vídeos.

## Extras que han salido por el camino
- **Parar en vez de pausar.** mpvpaper en pausa sigue gastando ~2,3 % de un
  núcleo (medido: 23 ticks/10 s con nvdec, vaapi y software por igual; es su
  bucle de render, no el decodificador). Con un juego delante se podría cerrar
  mpvpaper y relanzarlo en la misma posición (`start=<time-pos>`), dejando el
  frame de Noctalia debajo.
- `gamescope` en la lista de clases por defecto (tu windowrules.lua ya lo trata
  como juego).
- Opción para pausar también con ventanas maximizadas (`fullscreen` modo 1).
- Recargar `config.toml` sin reiniciar (SIGHUP o `nohland reload`).
- `nohland prev`.
- Pausa manual por monitor (`nohland pause -m`). Hoy la manual es global.
- Con batería, parar mpvpaper en vez de pausarlo (ver arriba): en el portátil
  ese ~2 % sí cuenta.
- Llevar a NoHland lo que hacía wallpaper.sh al cambiar de fondo: copiar al
  greeter, logo de fastfetch y color de OpenRGB. Encaja con el hook
  `wallpaper_changed` de Noctalia.
- Escenas de Wallpaper Engine (linux-wallpaperengine) como tercer backend, para
  retirar wallpaper.sh del todo. Hoy SUPER+ALT+W lo sigue lanzando y, si se
  usa, se apila con mpvpaper en la capa bottom.
- El selector de fondos de Noctalia (SUPER+SHIFT+W) cambia el fondo de Noctalia
  por debajo de NoHland sin que NoHland se entere.
