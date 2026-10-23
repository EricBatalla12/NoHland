"""El daemon: un bucle asyncio que solo se despierta cuando pasa algo.

Fuentes de eventos, ninguna sondeada:
  - socket2 de Hyprland  -> recalcula si hay que pausar por juego/pantalla completa
  - socket de control     -> ordenes del CLI (set, next, pause, resume, status)
  - fin del proceso mpvpaper

En reposo no hay temporizadores vivos, asi que el proceso se queda bloqueado
en epoll y no gasta CPU.

Pausa: hay dos motivos que se suman. La pausa manual (lienzo pause/resume) y
la automatica (lista de razones calculada desde Hyprland). El video esta
pausado si hay cualquiera de las dos; `resume` solo quita la manual.
"""

import asyncio
import json
import logging
import os
import signal
from pathlib import Path

from . import backends, config
from .backends import BackendError, MpvPaper
from .config import ANIMATED_EXT, CONTROL_SOCKET, IMAGE_EXT, RUNTIME_DIR, STATE_PATH, Config
from .hyprland import Hyprland, HyprlandError

log = logging.getLogger("lienzo")

# Eventos de socket2 tras los que puede haber cambiado la ventana activa o lo
# que esta a pantalla completa en algun monitor.
EVENTOS_RELEVANTES = {
    "activewindowv2", "fullscreen", "workspacev2", "focusedmonv2",
    "openwindow", "closewindow", "movewindowv2", "activespecialv2",
    "monitoraddedv2", "monitorremovedv2", "changefloatingmode",
}
# Los eventos llegan en rafagas (cerrar una ventana dispara tres o cuatro).
# Se espera este margen y se evalua una sola vez.
DEBOUNCE_S = 0.15


def tipo_de(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "imagen"
    if ext == ".gif":
        return "gif"
    if ext in ANIMATED_EXT:
        return "video"
    return None


class Daemon:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.hypr = Hyprland()
        self.current: Path | None = None
        self.player: MpvPaper | None = None
        self.manual_pause = False
        self.auto_reasons: list[str] = []
        self.error: str | None = None
        self._lock = asyncio.Lock()
        self._debounce: asyncio.TimerHandle | None = None
        self._stop = asyncio.Event()
        # asyncio solo guarda referencias debiles a las tareas sueltas
        self._tareas: set[asyncio.Task] = set()

    def _lanzar(self, coro) -> None:
        tarea = asyncio.create_task(coro)
        self._tareas.add(tarea)
        tarea.add_done_callback(self._tareas.discard)

    # -------------------------------------------------------- arranque

    async def run(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        if await _socket_vivo(CONTROL_SOCKET):
            raise SystemExit("ya hay un daemon de lienzo en marcha")
        CONTROL_SOCKET.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._cliente, path=CONTROL_SOCKET)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._stop.set)

        if not self.cfg.directory.is_dir():
            log.warning("la carpeta de fondos %s no existe", self.cfg.directory)

        await self._evaluar_pausa()
        await self._restaurar()
        eventos = asyncio.create_task(self._escuchar_hyprland())

        log.info("listo; control en %s", CONTROL_SOCKET)
        parada = asyncio.create_task(self._stop.wait())
        hecho, _ = await asyncio.wait({eventos, parada}, return_when=asyncio.FIRST_COMPLETED)

        server.close()
        eventos.cancel()
        if self.player:
            # Se suelta antes de pararlo para que _vigilar no lo cuente como
            # una caida inesperada
            player, self.player = self.player, None
            await player.stop()
        CONTROL_SOCKET.unlink(missing_ok=True)
        if eventos in hecho and eventos.exception():
            # Sin Hyprland no hay nada que hacer; systemd decide si reinicia
            raise eventos.exception()
        log.info("parado")

    async def _restaurar(self) -> None:
        try:
            ultimo = Path(json.loads(STATE_PATH.read_text())["current"])
        except (OSError, ValueError, KeyError, TypeError):
            log.info("no hay fondo guardado; espero a `lienzo set` o `lienzo next`")
            return
        if not ultimo.is_file():
            log.warning("el ultimo fondo (%s) ya no existe", ultimo)
            return
        try:
            # Al iniciar sesion Noctalia puede tardar en aparecer: hasta 20 s
            await self.apply(ultimo, intentos_noctalia=20)
        except (BackendError, ValueError) as e:
            log.error("no pude restaurar %s: %s", ultimo, e)

    # -------------------------------------------------------- cambio de fondo

    async def apply(self, path: Path, intentos_noctalia: int = 1) -> None:
        # abspath y no resolve(): si la carpeta tiene enlaces simbolicos, la
        # ruta debe seguir coincidiendo con la de _lista() para que next avance
        path = Path(os.path.abspath(path.expanduser()))
        tipo = tipo_de(path)
        if not path.is_file():
            raise ValueError(f"no existe: {path}")
        if tipo is None:
            raise ValueError(f"formato no soportado: {path.suffix or '(sin extension)'}")

        async with self._lock:
            self.error = None
            if tipo == "imagen":
                # Primero se quita el video para que se vea la transicion de
                # Noctalia; al reves, ocurriria escondida detras de mpvpaper.
                if self.player:
                    viejo, self.player = self.player, None
                    await viejo.stop()
                await backends.noctalia_set(path, intentos_noctalia)
            else:
                nuevo = MpvPaper(self.cfg, path)
                await nuevo.start(paused=self.paused)
                # El viejo se cierra cuando el nuevo ya pinta: sin parpadeo
                viejo, self.player = self.player, nuevo
                self._lanzar(self._vigilar(nuevo))
                if viejo:
                    await viejo.stop()

            self.current = path
            _guardar_estado(path)
            extra = ""
            if self.player:
                extra = f" (mpvpaper pid {self.player.proc.pid}{', en pausa' if self.paused else ''})"
            log.info("fondo: %s [%s]%s", path, tipo, extra)

    async def _vigilar(self, player: MpvPaper) -> None:
        codigo = await player.wait()
        # Si ya no es el reproductor actual, lo hemos parado nosotros
        if player is self.player:
            self.player = None
            self.error = f"mpvpaper termino inesperadamente (codigo {codigo})"
            log.error("%s reproduciendo %s", self.error, player.path)

    def _lista(self) -> list[Path]:
        if not self.cfg.directory.is_dir():
            return []
        return sorted(
            (p for p in self.cfg.directory.iterdir() if p.is_file() and tipo_de(p)),
            key=lambda p: p.name.lower(),
        )

    async def next(self) -> Path:
        lista = self._lista()
        if not lista:
            raise ValueError(f"no hay fondos en {self.cfg.directory}")
        try:
            i = lista.index(self.current) + 1
        except ValueError:
            i = 0
        siguiente = lista[i % len(lista)]
        await self.apply(siguiente)
        return siguiente

    # -------------------------------------------------------- pausa

    @property
    def paused(self) -> bool:
        return self.manual_pause or bool(self.auto_reasons)

    async def _sincronizar_pausa(self) -> None:
        if self.player and self.player.alive and self.player.paused != self.paused:
            try:
                await self.player.set_pause(self.paused)
            except (OSError, BackendError, asyncio.TimeoutError) as e:
                log.warning("no pude cambiar la pausa de mpv: %s", e)
                return
            if self.paused:
                log.info("pausado: %s", "; ".join(self.razones()))
            else:
                log.info("reanudado")

    def razones(self) -> list[str]:
        return (["pausa manual"] if self.manual_pause else []) + self.auto_reasons

    async def _evaluar_pausa(self) -> None:
        try:
            razones = await self._razones_automaticas()
        except (OSError, HyprlandError) as e:
            log.warning("no pude consultar Hyprland: %s", e)
            return
        if razones != self.auto_reasons:
            self.auto_reasons = razones
            if self.player is None:
                log.debug("razones de pausa (sin video): %s", razones)
        await self._sincronizar_pausa()

    async def _razones_automaticas(self) -> list[str]:
        razones = []
        if self.cfg.pause_on_fullscreen:
            monitores = await self.hypr.query("monitors")
            # Solo cuenta lo visible: el workspace activo de cada monitor y el
            # especial si esta abierto. Una ventana a pantalla completa en un
            # workspace escondido no tapa el fondo.
            visibles = {}
            for m in monitores:
                visibles[m["activeWorkspace"]["id"]] = m["name"]
                especial = m.get("specialWorkspace", {}).get("id", 0)
                if especial:
                    visibles[especial] = m["name"]
            for c in await self.hypr.query("clients"):
                # fullscreen es una mascara: 1 = maximizada, 2 = pantalla completa.
                # Maximizada deja ver el fondo por los huecos, asi que no cuenta.
                if c.get("fullscreen", 0) & 2 and c["workspace"]["id"] in visibles:
                    razones.append(f"pantalla completa: {c['class']} en {visibles[c['workspace']['id']]}")

        activa = await self.hypr.query("activewindow")
        if activa:
            clases = {activa.get("class", ""), activa.get("initialClass", "")} - {""}
            if any(p.search(c) for p in self.cfg.pause_classes for c in clases):
                razones.append(f"ventana de juego activa: {activa.get('class')}")
        return razones

    async def _escuchar_hyprland(self) -> None:
        loop = asyncio.get_running_loop()
        async for nombre, _ in self.hypr.events():
            if nombre not in EVENTOS_RELEVANTES:
                continue
            if self._debounce:
                self._debounce.cancel()
            self._debounce = loop.call_later(
                DEBOUNCE_S, lambda: self._lanzar(self._evaluar_pausa())
            )

    # -------------------------------------------------------- control

    async def _cliente(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            linea = await asyncio.wait_for(reader.readline(), 5)
            peticion = json.loads(linea)
            respuesta = await self._orden(peticion)
        except (ValueError, BackendError) as e:
            respuesta = {"ok": False, "error": str(e)}
        except Exception as e:  # una orden rota no debe tumbar el daemon
            log.exception("error atendiendo una orden")
            respuesta = {"ok": False, "error": f"error interno: {e}"}
        try:
            writer.write(json.dumps(respuesta, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()

    async def _orden(self, p: dict) -> dict:
        orden = p.get("cmd")
        if orden == "set":
            await self.apply(Path(p["path"]))
        elif orden == "next":
            await self.next()
        elif orden == "pause":
            self.manual_pause = True
            log.info("pausa manual pedida")
            await self._sincronizar_pausa()
        elif orden == "resume":
            self.manual_pause = False
            log.info("pausa manual retirada")
            await self._sincronizar_pausa()
        elif orden != "status":
            raise ValueError(f"orden desconocida: {orden!r}")
        return {"ok": True, "status": await self.status()}

    async def status(self) -> dict:
        tipo = tipo_de(self.current) if self.current else None
        estado = {
            "current": str(self.current) if self.current else None,
            "kind": tipo,
            "backend": None,
            "animated": tipo in ("video", "gif"),
            "paused": self.paused,
            "reasons": self.razones(),
            "directory": str(self.cfg.directory),
            "error": self.error,
        }
        if tipo == "imagen":
            estado["backend"] = "noctalia"
        elif self.player and self.player.alive:
            info = await self.player.info()
            estado["backend"] = "mpvpaper"
            estado["pid"] = self.player.proc.pid
            estado["hwdec"] = info.get("hwdec-current")
            estado["mpv_paused"] = info.get("pause")
        return estado


# ------------------------------------------------------------ utilidades

def _guardar_estado(path: Path) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"current": str(path)}, ensure_ascii=False))
    os.replace(tmp, STATE_PATH)


async def _socket_vivo(path: Path) -> bool:
    try:
        _, writer = await asyncio.open_unix_connection(path)
    except OSError:
        return False
    writer.close()
    return True


class _JournalFormatter(logging.Formatter):
    """Antepone <N> para que journald registre el nivel (warning, err...)."""

    NIVELES = {logging.DEBUG: 7, logging.INFO: 6, logging.WARNING: 4, logging.ERROR: 3, logging.CRITICAL: 2}

    def format(self, record: logging.LogRecord) -> str:
        return f"<{self.NIVELES.get(record.levelno, 6)}>{super().format(record)}"


def main() -> int:
    handler = logging.StreamHandler()
    if os.environ.get("JOURNAL_STREAM"):
        # journald ya pone fecha y nombre del servicio
        handler.setFormatter(_JournalFormatter("%(message)s"))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    logging.basicConfig(level=os.environ.get("LIENZO_LOG", "INFO").upper(), handlers=[handler])

    try:
        cfg, de_fichero = config.load()
    except config.ConfigError as e:
        log.error("config invalida: %s", e)
        return 1
    log.info("config: %s", config.CONFIG_PATH if de_fichero else "valores por defecto (no hay config.toml)")

    try:
        asyncio.run(Daemon(cfg).run())
    except HyprlandError as e:
        log.error("%s", e)
        return 1
    return 0
