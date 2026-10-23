"""El daemon: un bucle asyncio que solo se despierta cuando pasa algo.

Fuentes de eventos, ninguna sondeada:
  - socket2 de Hyprland  -> pantalla completa, ventana activa, monitores que
                             aparecen o desaparecen
  - uevents del kernel    -> enchufar o desenchufar el portatil
  - socket de control     -> ordenes del CLI (set, next, pause, resume, status)
  - fin de un proceso mpvpaper

En reposo no hay temporizadores vivos, asi que el proceso se queda bloqueado
en epoll y no gasta CPU.

Cada monitor tiene su fondo y, si es un video, su propio mpvpaper.

Pausa: un video esta pausado si hay CUALQUIER motivo. Unos son globales (pausa
manual, juego en la ventana activa, bateria) y otro es de cada monitor (una
ventana a pantalla completa en el workspace visible de ESE monitor). `resume`
solo quita la pausa manual.
"""

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path

from . import backends, config, energia
from .backends import BackendError, MpvPaper
from .config import ANIMATED_EXT, CONTROL_SOCKET, IMAGE_EXT, RUNTIME_DIR, STATE_PATH, Config
from .hyprland import Hyprland, HyprlandError

log = logging.getLogger("nohland")

# Eventos de socket2 tras los que puede haber cambiado la ventana activa o lo
# que esta a pantalla completa en algun monitor.
EVENTOS_RELEVANTES = {
    "activewindowv2", "fullscreen", "workspacev2", "focusedmonv2",
    "openwindow", "closewindow", "movewindowv2", "activespecialv2",
    "monitoraddedv2", "monitorremovedv2", "changefloatingmode",
}
EVENTOS_MONITOR = {"monitoraddedv2", "monitorremovedv2"}
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


def animado(path: Path) -> bool:
    return tipo_de(path) in ("video", "gif")


@dataclass
class Pantalla:
    """Un monitor conectado y lo que se ve en el."""
    nombre: str
    id: int
    actual: Path | None = None
    player: MpvPaper | None = None
    # Lo que tiene Noctalia en este monitor: el fondo o el fotograma del video
    imagen_noctalia: Path | None = None
    # Razon de pausa propia de este monitor (ventana a pantalla completa)
    pantalla_completa: str | None = None
    error: str | None = None


@dataclass
class Guardado:
    """Lo que sobrevive a un reinicio, en ~/.local/state/nohland/state.json."""
    # Ultimo fondo de cada monitor, tambien de los que ahora no estan conectados
    monitores: dict[str, Path] = field(default_factory=dict)
    # Para un monitor que se conecta sin fondo propio
    ultimo: Path | None = None

    @classmethod
    def leer(cls) -> "Guardado":
        try:
            datos = json.loads(STATE_PATH.read_text())
        except (OSError, ValueError):
            return cls()
        if "current" in datos:  # formato de la fase 1: un fondo para todos
            return cls(ultimo=Path(datos["current"]))
        return cls(
            monitores={k: Path(v) for k, v in datos.get("monitors", {}).items()},
            ultimo=Path(datos["ultimo"]) if datos.get("ultimo") else None,
        )

    def escribir(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "monitors": {k: str(v) for k, v in self.monitores.items()},
            "ultimo": str(self.ultimo) if self.ultimo else None,
        }, ensure_ascii=False, indent=1))
        os.replace(tmp, STATE_PATH)


class Daemon:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.hypr = Hyprland()
        self.pantallas: dict[str, Pantalla] = {}
        self.guardado = Guardado.leer()
        self.manual_pause = False
        self.juego: str | None = None
        self.bateria = False
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
            raise SystemExit("ya hay un daemon de NoHland en marcha")
        CONTROL_SOCKET.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._cliente, path=CONTROL_SOCKET)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._stop.set)

        if not self.cfg.directory.is_dir():
            log.warning("la carpeta de fondos %s no existe", self.cfg.directory)

        for m in await self.hypr.query("monitors"):
            self.pantallas[m["name"]] = Pantalla(m["name"], m["id"])
        log.info("monitores: %s; paleta desde %s", ", ".join(self.pantallas), self.monitor_paleta())

        if self.cfg.pause_on_battery:
            self._actualizar_bateria(inicial=True)
        await self._evaluar_pausa()
        # Al iniciar sesion Noctalia puede tardar en aparecer: hasta 20 s
        async with self._lock:
            await self._restaurar(list(self.pantallas), intentos=20)

        vigilantes = {asyncio.create_task(self._escuchar_hyprland())}
        if self.cfg.pause_on_battery:
            vigilantes.add(asyncio.create_task(self._escuchar_energia()))

        log.info("listo; control en %s", CONTROL_SOCKET)
        parada = asyncio.create_task(self._stop.wait())
        hecho, _ = await asyncio.wait(vigilantes | {parada}, return_when=asyncio.FIRST_COMPLETED)

        server.close()
        for t in vigilantes:
            t.cancel()
        for p in self.pantallas.values():
            await self._soltar_player(p)
        CONTROL_SOCKET.unlink(missing_ok=True)
        for t in hecho & vigilantes:
            if t.exception():
                # Sin Hyprland no hay nada que hacer; systemd decide si reinicia
                raise t.exception()
        log.info("parado")

    def _fondo_guardado(self, monitor: str) -> Path | None:
        return self.guardado.monitores.get(monitor) or self.guardado.ultimo

    async def _restaurar(self, monitores: list[str], intentos: int = 1) -> None:
        """Vuelve a poner en estos monitores lo que tenian guardado."""
        asignaciones = {}
        for nombre in monitores:
            fondo = self._fondo_guardado(nombre)
            if fondo is None:
                log.info("%s: sin fondo guardado; espero a `nohland set` o `nohland next`", nombre)
            elif not fondo.is_file():
                log.warning("%s: el ultimo fondo (%s) ya no existe", nombre, fondo)
            else:
                asignaciones[nombre] = fondo
        if asignaciones:
            try:
                await self._aplicar(asignaciones, intentos)
            except (BackendError, ValueError) as e:
                log.error("no pude restaurar los fondos: %s", e)

    # -------------------------------------------------------- monitores

    def monitor_paleta(self) -> str | None:
        """El monitor cuyo fondo da los colores a Noctalia."""
        if self.cfg.palette_monitor in self.pantallas:
            return self.cfg.palette_monitor
        if not self.pantallas:
            return None
        return min(self.pantallas.values(), key=lambda p: p.id).nombre

    async def _sincronizar_monitores(self) -> None:
        """Se llama al conectar o desconectar un monitor."""
        try:
            conectados = {m["name"]: m["id"] for m in await self.hypr.query("monitors")}
        except (OSError, HyprlandError) as e:
            log.warning("no pude consultar los monitores: %s", e)
            return
        async with self._lock:
            for nombre in set(self.pantallas) - set(conectados):
                log.info("%s: desconectado", nombre)
                await self._soltar_player(self.pantallas.pop(nombre))
            nuevos = [n for n in conectados if n not in self.pantallas]
            for nombre in nuevos:
                log.info("%s: conectado", nombre)
                self.pantallas[nombre] = Pantalla(nombre, conectados[nombre])
            if nuevos:
                await self._restaurar(nuevos)
        await self._evaluar_pausa()

    async def _soltar_player(self, p: Pantalla) -> None:
        # Se suelta antes de pararlo para que _vigilar no lo cuente como caida
        if p.player:
            player, p.player = p.player, None
            await player.stop()

    # -------------------------------------------------------- cambio de fondo

    def _destinos(self, monitor: str | None) -> list[str]:
        if monitor is None:
            return list(self.pantallas)
        if monitor not in self.pantallas:
            raise ValueError(f"monitor desconocido: {monitor} (conectados: {', '.join(self.pantallas)})")
        return [monitor]

    async def set(self, path: Path, monitor: str | None) -> None:
        # abspath y no resolve(): si la carpeta tiene enlaces simbolicos, la
        # ruta debe seguir coincidiendo con la de _lista() para que next avance
        path = Path(os.path.abspath(path.expanduser()))
        if not path.is_file():
            raise ValueError(f"no existe: {path}")
        if tipo_de(path) is None:
            raise ValueError(f"formato no soportado: {path.suffix or '(sin extension)'}")
        destinos = self._destinos(monitor)
        async with self._lock:
            await self._aplicar({m: path for m in destinos})

    async def next(self, monitor: str | None) -> None:
        """Cada monitor avanza por su propia carpeta."""
        asignaciones = {}
        for nombre in self._destinos(monitor):
            carpeta = config.directorio_de(self.cfg, nombre)
            lista = _lista(carpeta)
            if not lista:
                raise ValueError(f"no hay fondos en {carpeta}")
            try:
                i = lista.index(self.pantallas[nombre].actual) + 1
            except ValueError:
                i = 0
            asignaciones[nombre] = lista[i % len(lista)]
        async with self._lock:
            await self._aplicar(asignaciones)

    async def _aplicar(self, asignaciones: dict[str, Path], intentos: int = 1) -> None:
        """Pone asignaciones[monitor] en cada monitor. Llamar con _lock cogido."""
        # 1) Un fotograma por video distinto, aunque vaya a varios monitores
        frames: dict[Path, Path | None] = {}
        for path in set(asignaciones.values()):
            if animado(path):
                try:
                    frames[path] = await backends.extraer_frame(path)
                except (OSError, BackendError) as e:
                    log.warning("sin fotograma para la paleta: %s", e)
                    frames[path] = None

        # 2) Videos: el mpvpaper nuevo arranca antes de cerrar el viejo, sin parpadeo
        for nombre, path in asignaciones.items():
            p = self.pantallas[nombre]
            p.error = None
            if animado(path):
                nuevo = MpvPaper(self.cfg, path, nombre)
                try:
                    await nuevo.start(paused=self.pausado(p))
                except BackendError as e:
                    p.error = str(e)
                    raise
                viejo, p.player = p.player, nuevo
                self._lanzar(self._vigilar(p, nuevo))
                if viejo:
                    await viejo.stop()

        # 3) Noctalia. La paleta sale del ultimo wallpaper-set, asi que el
        #    monitor de la paleta va el ultimo. Si no estaba entre los cambiados,
        #    se le repite el suyo para que la paleta no se vaya a otro monitor.
        paleta = self.monitor_paleta()
        orden = sorted(asignaciones, key=lambda n: n == paleta)
        if paleta and paleta not in asignaciones and self.pantallas[paleta].imagen_noctalia:
            orden.append(paleta)
        for nombre in orden:
            p = self.pantallas[nombre]
            path = asignaciones.get(nombre)
            if path is None:  # el repaso del monitor de la paleta
                imagen = p.imagen_noctalia
            elif animado(path):
                imagen = frames[path]
            else:
                # Primero se quita el video para que se vea la transicion de
                # Noctalia; al reves, ocurriria escondida detras de mpvpaper.
                await self._soltar_player(p)
                imagen = path
            if imagen:
                await backends.noctalia_set(imagen, nombre, intentos)
                p.imagen_noctalia = imagen

        # 4) Estado
        for nombre, path in asignaciones.items():
            p = self.pantallas[nombre]
            p.actual = path
            self.guardado.monitores[nombre] = path
            extra = f", mpvpaper pid {p.player.proc.pid}" if p.player else ""
            pausa = ", en pausa" if p.player and self.pausado(p) else ""
            log.info("%s: %s [%s%s%s]", nombre, path, tipo_de(path), extra, pausa)
        self.guardado.ultimo = asignaciones.get(paleta) or next(iter(asignaciones.values()))
        self.guardado.escribir()
        backends.limpiar_frames({p.imagen_noctalia for p in self.pantallas.values() if p.imagen_noctalia})
        if paleta in orden:
            log.info("paleta desde %s: %s", paleta, self.pantallas[paleta].imagen_noctalia)

    async def _vigilar(self, p: Pantalla, player: MpvPaper) -> None:
        codigo = await player.wait()
        # Si ya no es el reproductor actual, lo hemos parado nosotros
        if player is not p.player:
            return
        p.player = None
        if p.nombre not in self.pantallas:
            return  # el monitor se ha ido y mpvpaper con el
        p.error = f"mpvpaper termino inesperadamente (codigo {codigo})"
        log.error("%s: %s reproduciendo %s", p.nombre, p.error, player.path)

    # -------------------------------------------------------- pausa

    def razones(self, p: Pantalla) -> list[str]:
        r = []
        if self.manual_pause:
            r.append("pausa manual")
        if self.juego:
            r.append(self.juego)
        if self.bateria:
            r.append("funcionando con bateria")
        if p.pantalla_completa:
            r.append(p.pantalla_completa)
        return r

    def pausado(self, p: Pantalla) -> bool:
        return bool(self.razones(p))

    async def _sincronizar_pausa(self) -> None:
        for p in list(self.pantallas.values()):
            quiere = self.pausado(p)
            if not (p.player and p.player.alive) or p.player.paused == quiere:
                continue
            try:
                await p.player.set_pause(quiere)
            except (OSError, BackendError, asyncio.TimeoutError) as e:
                log.warning("%s: no pude cambiar la pausa de mpv: %s", p.nombre, e)
                continue
            if quiere:
                log.info("%s: pausado: %s", p.nombre, "; ".join(self.razones(p)))
            else:
                log.info("%s: reanudado", p.nombre)

    async def _evaluar_pausa(self) -> None:
        try:
            await self._consultar_hyprland()
        except (OSError, HyprlandError) as e:
            log.warning("no pude consultar Hyprland: %s", e)
            return
        await self._sincronizar_pausa()

    async def _consultar_hyprland(self) -> None:
        completas: dict[str, str] = {}
        if self.cfg.pause_on_fullscreen:
            # Solo cuenta lo visible: el workspace activo de cada monitor y el
            # especial si esta abierto. Una ventana a pantalla completa en un
            # workspace escondido no tapa el fondo.
            visibles = {}
            for m in await self.hypr.query("monitors"):
                visibles[m["activeWorkspace"]["id"]] = m["name"]
                especial = m.get("specialWorkspace", {}).get("id", 0)
                if especial:
                    visibles[especial] = m["name"]
            for c in await self.hypr.query("clients"):
                # fullscreen es una mascara: 1 = maximizada, 2 = pantalla completa.
                # Maximizada deja ver el fondo por los huecos, asi que no cuenta.
                monitor = visibles.get(c["workspace"]["id"])
                if c.get("fullscreen", 0) & 2 and monitor:
                    completas.setdefault(monitor, f"pantalla completa: {c['class']}")
        for p in self.pantallas.values():
            p.pantalla_completa = completas.get(p.nombre)

        self.juego = None
        activa = await self.hypr.query("activewindow")
        if activa:
            clases = {activa.get("class", ""), activa.get("initialClass", "")} - {""}
            if any(pat.search(c) for pat in self.cfg.pause_classes for c in clases):
                self.juego = f"juego en la ventana activa: {activa.get('class')}"

    async def _escuchar_hyprland(self) -> None:
        loop = asyncio.get_running_loop()
        async for nombre, _ in self.hypr.events():
            if nombre in EVENTOS_MONITOR:
                self._lanzar(self._sincronizar_monitores())
            elif nombre in EVENTOS_RELEVANTES:
                if self._debounce:
                    self._debounce.cancel()
                self._debounce = loop.call_later(
                    DEBOUNCE_S, lambda: self._lanzar(self._evaluar_pausa())
                )

    def _actualizar_bateria(self, inicial: bool = False) -> bool:
        estado = energia.en_bateria()
        if inicial:
            log.info("energia: %s", {None: "sin bateria (la pausa por bateria no aplica)",
                                     True: "con bateria", False: "enchufado"}[estado])
        nuevo = bool(estado)
        cambio = nuevo != self.bateria
        self.bateria = nuevo
        if cambio and not inicial:
            log.info("energia: %s", "con bateria" if nuevo else "enchufado")
        return cambio

    async def _escuchar_energia(self) -> None:
        try:
            async for _ in energia.cambios():
                if self._actualizar_bateria():
                    await self._sincronizar_pausa()
        except OSError as e:
            # No es motivo para tumbar el daemon: se pierde solo esta funcion
            log.error("no puedo escuchar los cambios de energia: %s", e)
            await asyncio.Event().wait()

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
        monitor = p.get("monitor")
        if orden == "set":
            await self.set(Path(p["path"]), monitor)
        elif orden == "next":
            await self.next(monitor)
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
        paleta = self.monitor_paleta()
        monitores = []
        for p in sorted(self.pantallas.values(), key=lambda p: p.id):
            tipo = tipo_de(p.actual) if p.actual else None
            m = {
                "name": p.nombre,
                "current": str(p.actual) if p.actual else None,
                "kind": tipo,
                "backend": "noctalia" if tipo == "imagen" else None,
                "palette": p.nombre == paleta,
                "noctalia_image": str(p.imagen_noctalia) if p.imagen_noctalia else None,
                "paused": self.pausado(p),
                "reasons": self.razones(p),
                "directory": str(config.directorio_de(self.cfg, p.nombre)),
                "error": p.error,
            }
            if p.player and p.player.alive:
                info = await p.player.info()
                m.update(backend="mpvpaper", pid=p.player.proc.pid,
                         hwdec=info.get("hwdec-current"), mpv_paused=info.get("pause"))
            monitores.append(m)
        return {"monitors": monitores, "palette_monitor": paleta,
                "directory": str(self.cfg.directory),
                "manual_pause": self.manual_pause, "on_battery": self.bateria}


# ------------------------------------------------------------ utilidades

def _lista(carpeta: Path) -> list[Path]:
    if not carpeta.is_dir():
        return []
    return sorted(
        (p for p in carpeta.iterdir() if p.is_file() and tipo_de(p)),
        key=lambda p: p.name.lower(),
    )


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
    logging.basicConfig(level=os.environ.get("NOHLAND_LOG", "INFO").upper(), handlers=[handler])

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
