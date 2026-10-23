# TODO

Ideas apuntadas para no ampliar el alcance. Nada de esto está hecho.

## Fase 2 (pedida)
- Paleta de Noctalia desde un frame del vídeo (ffmpeg) al poner un vídeo.
- Un fondo por monitor.
- Pausa con batería en el portátil.

## Fase 3 (solo si se pide)
- Selector visual (Noctalia o rofi con miniaturas).
- Rotación automática cada X minutos.

## Extras que han salido por el camino
- **Parar en vez de pausar.** mpvpaper en pausa sigue gastando ~2,3 % de un
  núcleo (medido: 23 ticks/10 s con nvdec, vaapi y software por igual; es su
  bucle de render, no el decodificador). Con un juego delante se podría cerrar
  mpvpaper y relanzarlo en la misma posición (`start=<time-pos>`), dejando el
  frame de Noctalia debajo.
- `gamescope` en la lista de clases por defecto (tu windowrules.lua ya lo trata
  como juego).
- Opción para pausar también con ventanas maximizadas (`fullscreen` modo 1).
- Recargar `config.toml` sin reiniciar (SIGHUP o `lienzo reload`).
- `lienzo prev`.
- Llevar a lienzo lo que hacía wallpaper.sh al cambiar de fondo: copiar al
  greeter, logo de fastfetch y color de OpenRGB. Encaja con el hook
  `wallpaper_changed` de Noctalia.
- Escenas de Wallpaper Engine (linux-wallpaperengine) como tercer backend, para
  retirar wallpaper.sh del todo. Hoy SUPER+ALT+W lo sigue lanzando y, si se
  usa, se apila con mpvpaper en la capa bottom.
- El selector de fondos de Noctalia (SUPER+SHIFT+W) cambia el fondo de Noctalia
  por debajo de lienzo sin que lienzo se entere.
