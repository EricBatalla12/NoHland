"""`nohland doctor`: revisa que el equipo tenga lo que NoHland necesita.

Pensado para instalarlo en un equipo nuevo: dice que falta y como arreglarlo,
sin tocar nada. Codigo de salida 0 si no hay ningun fallo (los avisos no
cuentan como fallo).
"""

import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from . import config

OK, AVISO, FALLO = "ok", "aviso", "fallo"
MARCA = {OK: "✔", AVISO: "!", FALLO: "✖"}
# Versiones minimas que usa la interfaz (ToggleGroup llego en libadwaita 1.7)
ADW_MINIMA = (1, 7)
GTK_MINIMA = (4, 12)


def _salida(*cmd: str) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return r.returncode, (r.stdout + r.stderr).strip()


def _toml_noctalia() -> dict:
    """Junta las secciones [wallpaper] y [theme] de la config de Noctalia:
    los *.toml de ~/.config/noctalia y, por encima, el settings.toml de la UI."""
    datos: dict = {}
    base = Path(os.environ.get("XDG_CONFIG_HOME", config.HOME / ".config")) / "noctalia"
    estado = Path(os.environ.get("XDG_STATE_HOME", config.HOME / ".local/state")) / "noctalia" / "settings.toml"
    ficheros = sorted(base.glob("*.toml")) if base.is_dir() else []
    for f in [*ficheros, estado]:
        try:
            with f.open("rb") as fh:
                t = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        for seccion in ("wallpaper", "theme"):
            if isinstance(t.get(seccion), dict):
                datos.setdefault(seccion, {}).update(t[seccion])
    return datos


def revisar() -> list[tuple[str, str, str, str]]:
    """Lista de (estado, que, detalle, como arreglarlo)."""
    r = []

    def anota(estado, que, detalle="", arreglo=""):
        r.append((estado, que, detalle, arreglo))

    # --- Hyprland
    firma = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not shutil.which("hyprctl"):
        anota(FALLO, "Hyprland", "no está instalado", "NoHland solo funciona sobre Hyprland")
    elif not firma:
        anota(FALLO, "Hyprland", "esta terminal no está dentro de una sesión de Hyprland",
              "ejecútalo desde una terminal abierta en Hyprland")
    else:
        _, v = _salida("hyprctl", "version")
        anota(OK, "Hyprland", (v.splitlines() or ["?"])[0].split(" built")[0])

    # --- Noctalia
    if not shutil.which("noctalia"):
        anota(FALLO, "Noctalia", "no está instalado",
              "instala Noctalia 5 (en CachyOS: pacman -S noctalia; en Arch, desde el AUR)")
    else:
        _, v = _salida("noctalia", "--version")
        m = re.search(r"v?(\d+)\.(\d+)", v)
        if m and int(m.group(1)) < 5:
            anota(FALLO, "Noctalia", f"versión {m.group(0)}: hace falta la 5 o posterior "
                  "(la nativa, no la antigua de Quickshell)", "actualiza Noctalia")
        else:
            anota(OK, "Noctalia", v.splitlines()[0] if v else "?")
            rc, _ = _salida("noctalia", "msg", "status")
            if rc != 0:
                anota(AVISO, "Noctalia en marcha", "no responde a `noctalia msg`",
                      "arranca Noctalia (las imágenes y la paleta las pone él)")
        cfg_noct = _toml_noctalia()
        if cfg_noct.get("wallpaper", {}).get("enabled") is False:
            anota(FALLO, "Fondo de Noctalia", "[wallpaper] enabled = false",
                  "NoHland usa Noctalia para dibujar las imágenes: pon enabled = true")
        fuente = cfg_noct.get("theme", {}).get("source")
        if fuente and fuente != "wallpaper":
            anota(AVISO, "Paleta de Noctalia", f'[theme] source = "{fuente}"',
                  'con source = "wallpaper" los colores siguen al fondo de NoHland')

    # --- programas
    for prog, paquete, para in (("mpvpaper", "mpvpaper", "los fondos animados"),
                                ("ffmpeg", "ffmpeg", "miniaturas y paleta de vídeos"),
                                ("ffprobe", "ffmpeg", "miniaturas y paleta de vídeos")):
        if shutil.which(prog):
            anota(OK, prog)
        else:
            anota(FALLO, prog, f"falta (sirve para {para})", f"instala el paquete {paquete}")

    # --- decodificacion
    cfg, _ = config.load()
    nvidia = Path("/proc/driver/nvidia/version").exists()
    grafica = "NVIDIA" if nvidia else "AMD/Intel u otra"
    if cfg.hwdec == "nvdec" and not nvidia:
        anota(AVISO, "Decodificación", "hwdec = nvdec pero no hay driver de NVIDIA",
              'pon hwdec = "auto-safe" en Ajustes o en config.toml')
    else:
        anota(OK, "Decodificación", f"hwdec = {cfg.hwdec} · gráfica {grafica}")

    # --- interfaz
    if sys.version_info < (3, 11):
        anota(FALLO, "Python", f"{sys.version.split()[0]}: hace falta 3.11 o posterior")
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk
        gtk = (Gtk.get_major_version(), Gtk.get_minor_version())
        adw = (Adw.get_major_version(), Adw.get_minor_version())
        if gtk < GTK_MINIMA or adw < ADW_MINIMA:
            anota(AVISO, "Interfaz", f"GTK {gtk[0]}.{gtk[1]} y libadwaita {adw[0]}.{adw[1]} son viejos "
                  f"(hace falta GTK {GTK_MINIMA[0]}.{GTK_MINIMA[1]} y libadwaita {ADW_MINIMA[0]}.{ADW_MINIMA[1]})",
                  "el daemon y el CLI funcionan; `nohland gui` no")
        else:
            anota(OK, "Interfaz", f"GTK {gtk[0]}.{gtk[1]} · libadwaita {adw[0]}.{adw[1]}")
    except (ImportError, ValueError) as e:
        anota(AVISO, "Interfaz", f"no se puede cargar GTK 4 / libadwaita ({e})",
              "instala python-gobject, gtk4 y libadwaita para usar `nohland gui`")

    # --- servicio
    unidad = Path(os.environ.get("XDG_CONFIG_HOME", config.HOME / ".config")) / "systemd/user/nohland.service"
    if not unidad.exists():
        anota(FALLO, "Servicio", "no está instalado", "ejecuta ./install.sh desde el repo")
    else:
        _, activo = _salida("systemctl", "--user", "is-active", "nohland")
        _, habilitado = _salida("systemctl", "--user", "is-enabled", "nohland")
        estado = OK if activo == "active" and habilitado == "enabled" else AVISO
        detalle = f"{activo} · {habilitado}"
        if activo == "active" and habilitado != "enabled":
            detalle += " (no arrancará solo al iniciar sesión)"
        anota(estado, "Servicio", detalle,
              "" if estado == OK else "pulsa «Arrancar» en la ventana, o systemctl --user enable --now nohland")
        _, entorno = _salida("systemctl", "--user", "show-environment")
        if "HYPRLAND_INSTANCE_SIGNATURE=" not in entorno:
            anota(FALLO, "Entorno del servicio", "systemd no conoce la sesión de Hyprland (¿sin UWSM?)",
                  "añade al autostart de Hyprland: dbus-update-activation-environment --systemd --all "
                  "&& systemctl --user start nohland (ver README, «Sin UWSM»)")

    # --- inicio de sesion (opcional)
    from . import greeter
    if shutil.which("noctalia-greeter"):
        if greeter.disponible():
            anota(OK, "Inicio de sesión", f"NoHland puede cambiar su fondo ({greeter.FICHERO})")
        else:
            anota(AVISO, "Inicio de sesión", "noctalia-greeter está, pero sin preparar para NoHland",
                  "opcional: sudo tools/preparar-greeter.sh (una vez)")

    # --- carpeta
    if cfg.directory.is_dir():
        n = sum(1 for p in cfg.directory.iterdir()
                if p.is_file() and p.suffix.lower() in config.IMAGE_EXT | config.ANIMATED_EXT)
        anota(OK if n else AVISO, "Carpeta de fondos", f"{config._con_tilde(cfg.directory)} · {n} fondos",
              "" if n else "copia fondos ahí o usa «Conseguir fondos» en la ventana")
    else:
        anota(AVISO, "Carpeta de fondos", f"{cfg.directory} no existe todavía", "el daemon la crea al arrancar")
    return r


def main() -> int:
    resultados = revisar()
    ancho = max(len(q) for _, q, _, _ in resultados)
    for estado, que, detalle, arreglo in resultados:
        print(f" {MARCA[estado]} {que:<{ancho}}  {detalle}")
        if arreglo and estado != OK:
            print(f"   {'':<{ancho}}  → {arreglo}")
    fallos = sum(1 for e, *_ in resultados if e == FALLO)
    avisos = sum(1 for e, *_ in resultados if e == AVISO)
    print()
    if fallos:
        print(f"{fallos} fallo(s) y {avisos} aviso(s): NoHland no funcionará hasta arreglar los fallos.")
    elif avisos:
        print(f"Todo lo necesario está; {avisos} aviso(s).")
    else:
        print("Todo en orden.")
    return 1 if fallos else 0
