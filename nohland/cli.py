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
        if m.get("workspace"):
            print(f"  Workspace {m['workspace']}: " + ("fondo propio" if m.get("workspace_own") else "fondo general"))
        print(f"  Carpeta: {m['directory']}")
    g = e.get("greeter")
    if g:
        if not g["available"]:
            print("Greeter: sin preparar (sudo tools/preparar-greeter.sh, una vez)")
        elif g["mode"] == "no":
            print("Greeter: no se toca")
        else:
            que = "como el escritorio" if g["mode"] == "escritorio" else Path(g["image"] or "?").name
            print(f"Greeter: {que}")
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


def _exportar(destino: Path) -> int:
    # No pasa por el daemon: solo lee la config y state.json
    from . import config, paquete
    from .daemon import Guardado
    if destino.suffix.lower() != ".zip":
        destino = destino.with_name(destino.name + ".zip")
    c = paquete.exportar(config.load()[0], Guardado.leer(), destino)
    print(f"Exportados {len(c.fondos)} fondos ({paquete.tamano(c.bytes)}) en {destino}")
    if c.steam:
        print(f"Los fondos de Steam (Wallpaper Engine) no se pasan: se han quedado fuera {len(c.steam)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nohland", description="Gestor de fondos para Hyprland + Noctalia")
    ap.add_argument("--version", action="version", version=f"nohland {__version__}")
    sub = ap.add_subparsers(dest="orden", required=True, metavar="orden")
    sub.add_parser("daemon", help="arranca el daemon (lo usa el servicio systemd)")
    sub.add_parser("gui", help="abre la ventana de NoHland")
    sub.add_parser("doctor", help="revisa que el equipo tenga lo que NoHland necesita")
    p_set = sub.add_parser("set", help="pone un fondo (imagen, mp4, webm o gif)")
    p_set.add_argument("archivo", type=Path)
    solo = p_set.add_mutually_exclusive_group()
    solo.add_argument("-w", "--workspace", action="store_true",
                      help="solo en el workspace visible (su fondo propio)")
    solo.add_argument("-g", "--general", action="store_true",
                      help="como fondo general; todos los workspaces dejan su fondo propio")
    p_next = sub.add_parser("next", help="pasa al siguiente fondo de la carpeta")
    p_ws = sub.add_parser("workspace", help="fondo propio del workspace visible",
                          description="clear: el workspace visible vuelve a usar el fondo general del monitor")
    p_ws.add_argument("accion", choices=["clear"])
    for p in (p_set, p_next, p_ws):
        p.add_argument("-m", "--monitor", help="solo en este monitor (p. ej. HDMI-A-1); por defecto, en todos")
    sub.add_parser("pause", help="pausa la animacion hasta `nohland resume`")
    sub.add_parser("resume", help="quita la pausa manual")
    p_rot = sub.add_parser(
        "rotate", help="rotacion automatica",
        description="on/off la enciende o apaga; every <minutos> cambia el tiempo; order orden|aleatorio; "
                    "exclude/include <archivo> lo quita o lo vuelve a meter en la rotacion")
    p_rot.add_argument("accion", choices=["on", "off", "every", "order", "exclude", "include"])
    p_rot.add_argument("valor", nargs="?")
    p_gre = sub.add_parser("greeter", help="fondo de la pantalla de inicio de sesion",
                           description="escritorio: el mismo que el escritorio; fija <archivo>: una imagen o "
                                       "video concreto; no: no tocarlo")
    p_gre.add_argument("modo", choices=["escritorio", "fija", "no"])
    p_gre.add_argument("archivo", nargs="?", type=Path)
    p_exp = sub.add_parser("export", help="guarda los fondos en un .zip para otro equipo",
                           description="los fondos de las carpetas y lo elegido (fondo general, el de cada "
                                       "workspace, excluidos de la rotacion); los de Steam no se pasan")
    p_exp.add_argument("archivo", type=Path)
    p_imp = sub.add_parser("import", help="trae los fondos de un .zip hecho con `nohland export`")
    p_imp.add_argument("archivo", type=Path)
    p_status = sub.add_parser("status", help="que se reproduce y si esta pausado")
    p_status.add_argument("--json", action="store_true", help="salida en JSON")
    args = ap.parse_args(argv)

    if args.orden == "daemon":
        from .daemon import main as daemon_main
        return daemon_main()
    if args.orden == "doctor":
        from .doctor import main as doctor_main
        return doctor_main()
    if args.orden == "gui":
        # Import tardio: el CLI y el daemon no necesitan GTK
        from .gui import main as gui_main
        return gui_main()

    if args.orden == "export":
        return _exportar(args.archivo)

    peticion = {"cmd": "workspace-clear" if args.orden == "workspace" else args.orden}
    if getattr(args, "workspace", False):
        peticion["workspace"] = True
    if getattr(args, "general", False):
        peticion["general"] = True
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
    if args.orden == "greeter":
        peticion["mode"] = args.modo
        if args.archivo:
            peticion["image"] = os.path.abspath(args.archivo.expanduser())
        elif args.modo == "fija":
            ap.error("greeter fija necesita un archivo")
    if getattr(args, "monitor", None):
        peticion["monitor"] = args.monitor
    if args.orden in ("set", "import"):
        # La ruta se resuelve aqui: el daemon no comparte el directorio actual
        peticion["path"] = os.path.abspath(args.archivo.expanduser())

    try:
        # Importar copia ficheros que pueden sumar algun GB
        respuesta = enviar(peticion, timeout=1800 if args.orden == "import" else 30)
    except DaemonParado as e:
        sys.exit(f"nohland: {e}")
    if not respuesta.get("ok"):
        print(f"nohland: {respuesta.get('error')}", file=sys.stderr)
        return 1
    if args.orden == "import":
        print(f"Importados {respuesta['imported']} fondos en {respuesta['folder']}")
    if args.orden == "status" and args.json:
        print(json.dumps(respuesta["status"], indent=2, ensure_ascii=False))
    else:
        imprimir_estado(respuesta["status"])
    return 0
