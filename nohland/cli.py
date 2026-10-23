"""CLI de NoHland. Todo salvo `daemon` es un cliente del socket de control."""

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .cliente import DaemonParado, enviar


def imprimir_estado(e: dict) -> None:
    for m in e["monitors"]:
        cabecera = m["name"] + ("  (de aqui sale la paleta)" if m["palette"] else "")
        print(cabecera)
        if not m["current"]:
            print("  Fondo:   ninguno")
        else:
            ruta = Path(m["current"])
            print(f"  Fondo:   {ruta.name}")
            print(f"  Ruta:    {ruta}")
            tipo = m["kind"]
            if m.get("backend") == "mpvpaper":
                # mpv devuelve None hasta que abre el decodificador y "no" si
                # decodifica por CPU (los GIF siempre)
                hwdec = {None: "arrancando", "no": "software"}.get(m.get("hwdec"), m.get("hwdec"))
                tipo += f" · mpvpaper (pid {m['pid']}) · decodificacion: {hwdec}"
            elif m.get("backend"):
                tipo += f" · {m['backend']}"
            print(f"  Tipo:    {tipo}")
            if m["kind"] != "imagen" and m.get("noctalia_image"):
                print(f"  Debajo:  fotograma {Path(m['noctalia_image']).name} (Noctalia: paleta y bloqueo)")
        if m.get("error"):
            print(f"  Error:   {m['error']}")
        if m.get("backend") == "mpvpaper":
            print(f"  Estado:  {'pausado' if m['paused'] else 'reproduciendo'}")
        elif m["current"]:
            print("  Estado:  imagen fija (nada que pausar)")
        for razon in m["reasons"]:
            # Las razones se muestran aunque el fondo sea fijo: asi se ve que la
            # deteccion funciona y que el siguiente video arrancara en pausa.
            print(f"    · {razon}")
        print(f"  Carpeta: {m['directory']}")
    r = e.get("rotation")
    if r:
        if not r["enabled"]:
            print("Rotacion: apagada")
        else:
            fuera = len(r.get("excluded", []))
            linea = f"Rotacion: cada {r['interval_minutes']:g} min · {r.get('order', 'orden')}"
            if fuera:
                linea += f" · {fuera} fuera de la rotacion"
            if r["next_in_s"] is not None:
                m, s = divmod(max(r["next_in_s"], 0), 60)
                linea += f" · siguiente cambio en {m} min {s:02d} s"
            if r["pending"]:
                linea += f" · esperando a que se quite la pausa en {', '.join(r['pending'])}"
            print(linea)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nohland", description="Gestor de fondos para Hyprland + Noctalia")
    ap.add_argument("--version", action="version", version=f"nohland {__version__}")
    sub = ap.add_subparsers(dest="orden", required=True, metavar="orden")
    sub.add_parser("daemon", help="arranca el daemon (lo usa el servicio systemd)")
    sub.add_parser("gui", help="abre la ventana de NoHland")
    p_set = sub.add_parser("set", help="pone un fondo (imagen, mp4, webm o gif)")
    p_set.add_argument("archivo", type=Path)
    p_next = sub.add_parser("next", help="pasa al siguiente fondo de la carpeta")
    for p in (p_set, p_next):
        p.add_argument("-m", "--monitor", help="solo en este monitor (p. ej. HDMI-A-1); por defecto, en todos")
    sub.add_parser("pause", help="pausa la animacion hasta `nohland resume`")
    sub.add_parser("resume", help="quita la pausa manual")
    p_rot = sub.add_parser(
        "rotate", help="rotacion automatica",
        description="on/off la enciende o apaga; every <minutos> cambia el tiempo; order orden|aleatorio; "
                    "exclude/include <archivo> lo quita o lo vuelve a meter en la rotacion")
    p_rot.add_argument("accion", choices=["on", "off", "every", "order", "exclude", "include"])
    p_rot.add_argument("valor", nargs="?")
    p_status = sub.add_parser("status", help="que se reproduce y si esta pausado")
    p_status.add_argument("--json", action="store_true", help="salida en JSON")
    args = ap.parse_args(argv)

    if args.orden == "daemon":
        from .daemon import main as daemon_main
        return daemon_main()
    if args.orden == "gui":
        # Import tardio: el CLI y el daemon no necesitan GTK
        from .gui import main as gui_main
        return gui_main()

    peticion = {"cmd": args.orden}
    if args.orden == "rotate":
        accion, valor = args.accion, args.valor
        if accion in ("on", "off"):
            peticion["enabled"] = accion == "on"
        elif valor is None:
            ap.error(f"rotate {accion} necesita un valor")
        elif accion == "every":
            try:
                peticion["minutes"] = float(valor.replace(",", "."))
            except ValueError:
                ap.error(f"rotate every: {valor!r} no es un numero de minutos")
        elif accion == "order":
            peticion["order"] = {"seq": "orden", "random": "aleatorio"}.get(valor, valor)
        else:
            clave = "exclude" if accion == "exclude" else "include"
            peticion[clave] = [os.path.abspath(Path(valor).expanduser())]
    if getattr(args, "monitor", None):
        peticion["monitor"] = args.monitor
    if args.orden == "set":
        # La ruta se resuelve aqui: el daemon no comparte el directorio actual
        peticion["path"] = os.path.abspath(args.archivo.expanduser())

    try:
        respuesta = enviar(peticion)
    except DaemonParado as e:
        sys.exit(f"nohland: {e}")
    if not respuesta.get("ok"):
        print(f"nohland: {respuesta.get('error')}", file=sys.stderr)
        return 1
    if args.orden == "status" and args.json:
        print(json.dumps(respuesta["status"], indent=2, ensure_ascii=False))
    else:
        imprimir_estado(respuesta["status"])
    return 0
