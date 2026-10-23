"""CLI de lienzo. Todo salvo `daemon` es un cliente del socket de control."""

import argparse
import json
import os
import socket
import sys
from pathlib import Path

from . import __version__
from .config import CONTROL_SOCKET


def enviar(peticion: dict) -> dict:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(30)
            s.connect(str(CONTROL_SOCKET))
            s.sendall(json.dumps(peticion).encode() + b"\n")
            datos = b""
            while trozo := s.recv(65536):
                datos += trozo
    except (FileNotFoundError, ConnectionRefusedError):
        sys.exit("lienzo: el daemon no esta en marcha (systemctl --user start lienzo)")
    except TimeoutError:
        sys.exit("lienzo: el daemon no ha respondido en 30 s")
    return json.loads(datos)


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lienzo", description="Gestor de fondos para Hyprland + Noctalia")
    ap.add_argument("--version", action="version", version=f"lienzo {__version__}")
    sub = ap.add_subparsers(dest="orden", required=True, metavar="orden")
    sub.add_parser("daemon", help="arranca el daemon (lo usa el servicio systemd)")
    p_set = sub.add_parser("set", help="pone un fondo (imagen, mp4, webm o gif)")
    p_set.add_argument("archivo", type=Path)
    p_next = sub.add_parser("next", help="pasa al siguiente fondo de la carpeta")
    for p in (p_set, p_next):
        p.add_argument("-m", "--monitor", help="solo en este monitor (p. ej. HDMI-A-1); por defecto, en todos")
    sub.add_parser("pause", help="pausa la animacion hasta `lienzo resume`")
    sub.add_parser("resume", help="quita la pausa manual")
    p_status = sub.add_parser("status", help="que se reproduce y si esta pausado")
    p_status.add_argument("--json", action="store_true", help="salida en JSON")
    args = ap.parse_args(argv)

    if args.orden == "daemon":
        from .daemon import main as daemon_main
        return daemon_main()

    peticion = {"cmd": args.orden}
    if getattr(args, "monitor", None):
        peticion["monitor"] = args.monitor
    if args.orden == "set":
        # La ruta se resuelve aqui: el daemon no comparte el directorio actual
        peticion["path"] = os.path.abspath(args.archivo.expanduser())

    respuesta = enviar(peticion)
    if not respuesta.get("ok"):
        print(f"lienzo: {respuesta.get('error')}", file=sys.stderr)
        return 1
    if args.orden == "status" and args.json:
        print(json.dumps(respuesta["status"], indent=2, ensure_ascii=False))
    else:
        imprimir_estado(respuesta["status"])
    return 0
