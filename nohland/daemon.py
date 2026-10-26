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

Rotacion: con el interruptor encendido, cada N minutos cada monitor pasa a
otro fondo de su carpeta, en orden o al azar, saltando los que se han
quitado de la rotacion. Todo eso (encendida, minutos, orden y excluidos) se
cambia desde la ventana y vive en state.json, no en config.toml: asi cambiarlo
no obliga a reiniciar el servicio. Es un unico temporizador (call_at), asi
que en reposo sigue sin haber nada que despierte el proceso. Un monitor en
pausa no cambia: queda pendiente y cambia cuando lleva GRACIA_ROTACION_S sin
pausa, para no hacerlo en un alt-tab de dos segundos fuera de un juego. Poner
un fondo a mano reinicia la cuenta.

Fondo por workspace: un workspace puede tener fondo propio (state.json,
por nombre de workspace). Al cambiar de workspace cada monitor pone el propio
del workspace visible o, si no tiene, el general del monitor. Elegir fondo
(set, next, rotacion) cambia el propio si el workspace visible lo tiene y el
general si no: asi el cambio siempre se ve. `set` con general=True cambia
el general y el workspace visible deja su fondo propio, para que se vea ya (si
solo se guardara, en un workspace con fondo propio no cambiaria nada en
pantalla y pareceria que el boton no funciona).

Pausa: un video esta pausado si hay CUALQUIER motivo. Unos son globales (pausa
manual, juego en la ventana activa, bateria) y otro es de cada monitor (una
ventana a pantalla completa en el workspace visible de ESE monitor). `resume`
solo quita la pausa manual.
"""

import asyncio
import json
import logging
import os
import random
import signal
from dataclasses import dataclass, field
from pathlib import Path

from . import backends, config, energia, greeter
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
# Tiempo sin pausa antes de hacer un cambio de rotacion que estaba pendiente
GRACIA_ROTACION_S = 30


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
    # Nombre del workspace visible en este monitor
    workspace: str | None = None


@dataclass
class Guardado:
    """Lo que sobrevive a un reinicio, en ~/.local/state/nohland/state.json."""
    # Ultimo fondo de cada monitor, tambien de los que ahora no estan conectados
    monitores: dict[str, Path] = field(default_factory=dict)
    # Para un monitor que se conecta sin fondo propio
    ultimo: Path | None = None
    # Rotacion automatica: interruptor, minutos (None = el de config.toml),
    # orden ("orden" o "aleatorio") y fondos que no entran
    rotacion: bool = False
    rotacion_minutos: float | None = None
    rotacion_orden: str = "orden"
    rotacion_excluidos: set[str] = field(default_factory=set)
    # Pantalla de inicio de sesion: "escritorio" (copia del fondo que da la
    # paleta), "fija" (greeter_imagen) o "no" (no se toca)
    greeter_modo: str = "no"
    greeter_imagen: str | None = None
    # La pausa manual sobrevive a reiniciar el servicio (guardar ajustes,
    # actualizar, iniciar sesion): antes se perdia en cada uno
    pausa_manual: bool = False
    # Fondo propio de cada workspace, por nombre ("1", "2", "gaming"...)
    workspaces: dict[str, str] = field(default_factory=dict)

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
            rotacion=bool(datos.get("rotation", False)),
            rotacion_minutos=datos.get("rotation_minutes"),
            rotacion_orden=datos.get("rotation_order", "orden"),
            rotacion_excluidos=set(datos.get("rotation_excluded", [])),
            greeter_modo=datos.get("greeter_mode", "no"),
            greeter_imagen=datos.get("greeter_image"),
            pausa_manual=bool(datos.get("manual_pause", False)),
            workspaces=dict(datos.get("workspaces", {})),
        )

    def escribir(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "monitors": {k: str(v) for k, v in self.monitores.items()},
            "ultimo": str(self.ultimo) if self.ultimo else None,
            "rotation": self.rotacion,
            "rotation_minutes": self.rotacion_minutos,
            "rotation_order": self.rotacion_orden,
            "rotation_excluded": sorted(self.rotacion_excluidos),
            "greeter_mode": self.greeter_modo,
            "greeter_image": self.greeter_imagen,
            "manual_pause": self.pausa_manual,
            "workspaces": self.workspaces,
        }, ensure_ascii=False, indent=1))
        os.replace(tmp, STATE_PATH)


class Daemon:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.hypr = Hyprland()
        self.pantallas: dict[str, Pantalla] = {}
        self.guardado = Guardado.leer()
        self.manual_pause = self.guardado.pausa_manual
        self.juego: str | None = None
        self.bateria = False
        self._lock = asyncio.Lock()
        self._debounce: asyncio.TimerHandle | None = None
        # Rotacion: cuando toca el proximo cambio (hora del bucle) y que
        # monitores esperan a que se les quite la pausa para cambiar
        self._rotacion_hasta: float | None = None
        self._rotacion_timer: asyncio.TimerHandle | None = None
        self._gracia_timer: asyncio.TimerHandle | None = None
        self.pendientes: set[str] = set()
        self._greeter_copiado: Path | None = None
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
            # En una instalacion nueva aun no existe: se crea para que la
            # ventana y `nohland next` tengan donde mirar
            try:
                self.cfg.directory.mkdir(parents=True)
                log.info("carpeta de fondos creada: %s", self.cfg.directory)
            except OSError as e:
                log.warning("la carpeta de fondos %s no existe y no pude crearla: %s", self.cfg.directory, e)

        for m in await self.hypr.query("monitors"):
            self.pantallas[m["name"]] = Pantalla(m["name"], m["id"])
        log.info("monitores: %s; paleta desde %s", ", ".join(self.pantallas), self.monitor_paleta())
        if self.manual_pause:
            log.info("pausa manual: sigue puesta de la sesion anterior")

        if self.cfg.pause_on_battery:
            self._actualizar_bateria(inicial=True)
        await self._evaluar_pausa()
        # Al iniciar sesion Noctalia puede tardar en aparecer: hasta 20 s
        async with self._lock:
            await self._restaurar(list(self.pantallas), intentos=20)
        self._lanzar(self._vigilar_noctalia_al_arrancar())

        if self.guardado.rotacion:
            log.info("rotacion: encendida, cada %g min", self.minutos_rotacion)
            self._programar_rotacion()

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
        """El fondo general del monitor (sin contar el de su workspace)."""
        return self.guardado.monitores.get(monitor) or self.guardado.ultimo

    def _propio(self, p: Pantalla) -> Path | None:
        ruta = self.guardado.workspaces.get(p.workspace) if p.workspace else None
        return Path(ruta) if ruta else None

    def _deseado(self, monitor: str) -> Path | None:
        """Lo que deberia verse: el del workspace visible o el general."""
        return self._propio(self.pantallas[monitor]) or self._fondo_guardado(monitor)

    def _repartir(self, asignaciones: dict[str, Path], solo_workspace: bool = False) -> set[str]:
        """Decide donde se guarda cada cambio: en el fondo propio del workspace
        visible (si ya lo tiene, o si se pide con solo_workspace) o en el
        general del monitor. Devuelve los monitores cuyo general cambia."""
        generales = set()
        for nombre, path in asignaciones.items():
            ws = self.pantallas[nombre].workspace
            if ws and (solo_workspace or ws in self.guardado.workspaces):
                self.guardado.workspaces[ws] = str(path)
            else:
                generales.add(nombre)
        return generales

    async def _restaurar(self, monitores: list[str], intentos: int = 1) -> None:
        """Vuelve a poner en estos monitores lo que tenian guardado."""
        asignaciones, generales = {}, set()
        for nombre in monitores:
            fondo = self._deseado(nombre)
            if not self._propio(self.pantallas[nombre]):
                generales.add(nombre)
            if fondo is None:
                log.info("%s: sin fondo guardado; espero a `nohland set` o `nohland next`", nombre)
            elif not fondo.is_file():
                log.warning("%s: el ultimo fondo (%s) ya no existe", nombre, fondo)
            else:
                asignaciones[nombre] = fondo
        if asignaciones:
            try:
                await self._aplicar(asignaciones, intentos, generales=generales)
            except (BackendError, ValueError) as e:
                log.error("no pude restaurar los fondos: %s", e)

    async def _vigilar_noctalia_al_arrancar(self) -> None:
        """Comprueba que Noctalia se ha quedado con los fondos de NoHland.

        Al iniciar sesion Noctalia y NoHland arrancan casi a la vez: Noctalia
        acepta el wallpaper-set (responde ok) pero, si aun se esta iniciando,
        luego carga el fondo que tenia guardado y lo pisa (pasaba: NoHland
        decia gw1vrl y en pantalla estaba el de la sesion anterior). Se mira
        unas cuantas veces durante el primer medio minuto y se repite el
        wallpaper-set donde no coincida, con el monitor de la paleta el ultimo.
        """
        for espera in (2, 3, 5, 10, 10):
            await asyncio.sleep(espera)
            paleta = self.monitor_paleta()
            distintos = []
            for nombre, p in self.pantallas.items():
                if p.imagen_noctalia and await backends.noctalia_get(nombre) != str(p.imagen_noctalia):
                    distintos.append(nombre)
            if not distintos:
                continue
            # Si se repite otro, la paleta se iria a el: el de la paleta, el ultimo
            orden = sorted(distintos, key=lambda n: n == paleta)
            if paleta and paleta not in orden and self.pantallas[paleta].imagen_noctalia:
                orden.append(paleta)
            async with self._lock:
                for nombre in orden:
                    p = self.pantallas.get(nombre)
                    if p and p.imagen_noctalia:
                        try:
                            await backends.noctalia_set(p.imagen_noctalia, nombre)
                        except BackendError as e:
                            log.warning("%s: no pude repetir el fondo en Noctalia: %s", nombre, e)
            log.info("Noctalia habia cambiado el fondo de %s al arrancar; repuesto", ", ".join(distintos))

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

    async def set(self, path: Path, monitor: str | None, solo_workspace: bool = False,
                  general: bool = False) -> None:
        # abspath y no resolve(): si la carpeta tiene enlaces simbolicos, la
        # ruta debe seguir coincidiendo con la de _lista() para que next avance
        path = Path(os.path.abspath(path.expanduser()))
        if not path.is_file():
            raise ValueError(f"no existe: {path}")
        if tipo_de(path) is None:
            raise ValueError(f"formato no soportado: {path.suffix or '(sin extension)'}")
        destinos = self._destinos(monitor)
        asignaciones = {m: path for m in destinos}
        async with self._lock:
            if general:
                await self._poner_general(asignaciones)
            else:
                await self._aplicar(asignaciones, generales=self._repartir(asignaciones, solo_workspace))
        self._cambio_manual(destinos)

    async def _poner_general(self, asignaciones: dict[str, Path]) -> None:
        """Cambia el fondo general y el workspace visible deja su fondo propio
        (los demas workspaces conservan el suyo). Llamar con _lock cogido."""
        for nombre in asignaciones:
            ws = self.pantallas[nombre].workspace
            if ws and self.guardado.workspaces.pop(ws, None):
                log.info("workspace %s: deja su fondo propio y usa el general", ws)
        await self._aplicar(asignaciones, generales=set(asignaciones))

    async def next(self, monitor: str | None) -> None:
        """Cada monitor avanza por su propia carpeta."""
        destinos = self._destinos(monitor)
        asignaciones = self._siguientes(destinos)
        for nombre in destinos:
            if nombre not in asignaciones:
                raise ValueError(f"no hay fondos en {config.directorio_de(self.cfg, nombre)}")
        async with self._lock:
            await self._aplicar(asignaciones, generales=self._repartir(asignaciones))
        self._cambio_manual(destinos)

    def _siguientes(self, monitores, rotacion: bool = False) -> dict[str, Path]:
        """El siguiente fondo de la carpeta de cada monitor (sin los de carpeta vacia).

        Con rotacion=True se saltan los excluidos de la rotacion y se respeta
        su orden; `next` a mano recorre la carpeta entera en orden.
        """
        asignaciones = {}
        for nombre in monitores:
            lista = _lista(config.directorio_de(self.cfg, nombre))
            actual = self.pantallas[nombre].actual
            if rotacion:
                candidatos = [f for f in lista if str(f) not in self.guardado.rotacion_excluidos]
                if self.guardado.rotacion_orden == "aleatorio":
                    # Al azar, pero nunca el mismo que ya esta puesto
                    otros = [f for f in candidatos if f != actual] or candidatos
                    if otros:
                        asignaciones[nombre] = random.choice(otros)
                    continue
                # En orden: el primero de la rotacion que venga despues del
                # actual en la carpeta, aunque el actual este excluido
                try:
                    i = lista.index(actual) + 1
                except ValueError:
                    i = 0
                vuelta = lista[i:] + lista[:i]
                siguiente = next((f for f in vuelta if f in candidatos), None)
                if siguiente:
                    asignaciones[nombre] = siguiente
                continue
            if not lista:
                continue
            try:
                i = lista.index(actual) + 1
            except ValueError:
                i = 0
            asignaciones[nombre] = lista[i % len(lista)]
        return asignaciones

    async def _aplicar(self, asignaciones: dict[str, Path], intentos: int = 1,
                       generales: set[str] | None = None) -> None:
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
            # generales: monitores cuyo fondo general cambia (None = todos). Con
            # el fondo de un workspace, el general del monitor no se toca
            if generales is None or nombre in generales:
                self.guardado.monitores[nombre] = path
            extra = f", mpvpaper pid {p.player.proc.pid}" if p.player else ""
            pausa = ", en pausa" if p.player and self.pausado(p) else ""
            log.info("%s: %s [%s%s%s]", nombre, path, tipo_de(path), extra, pausa)
        self.guardado.ultimo = asignaciones.get(paleta) or next(iter(asignaciones.values()))
        self.guardado.escribir()
        # Se guardan los fotogramas de todos los videos de las carpetas: la
        # rotacion volvera a ellos y sacarlos de nuevo cuesta CPU
        conservar = {p.imagen_noctalia for p in self.pantallas.values() if p.imagen_noctalia}
        for nombre in self.pantallas:
            for f in _lista(config.directorio_de(self.cfg, nombre)):
                if animado(f):
                    conservar.add(backends.ruta_frame(f))
        backends.limpiar_frames(conservar)
        if paleta in orden:
            log.info("paleta desde %s: %s", paleta, self.pantallas[paleta].imagen_noctalia)
        await self._actualizar_greeter()

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

    # -------------------------------------------------------- greeter

    async def _actualizar_greeter(self) -> None:
        """Copia al greeter la imagen que toca segun el modo.

        Se llama tras cada cambio de fondo; si la imagen es la misma que la
        ultima copiada, no hace nada. Es copiar un fichero, en un hilo.
        """
        modo = self.guardado.greeter_modo
        if modo == "no" or not greeter.disponible():
            return
        if modo == "fija" and self.guardado.greeter_imagen:
            origen = Path(self.guardado.greeter_imagen)
            if animado(origen):
                try:
                    origen = await backends.extraer_frame(origen)
                except (OSError, BackendError) as e:
                    log.warning("greeter: sin fotograma de %s: %s", origen.name, e)
                    return
        else:
            paleta = self.monitor_paleta()
            origen = self.pantallas[paleta].imagen_noctalia if paleta else None
        if not origen or not origen.is_file() or origen == self._greeter_copiado:
            return
        try:
            await asyncio.to_thread(greeter.copiar, origen)
        except OSError as e:
            log.warning("greeter: no pude copiar %s: %s", origen, e)
            return
        self._greeter_copiado = origen
        log.info("greeter: fondo de inicio de sesion -> %s", origen.name)

    async def poner_greeter(self, modo: str | None, imagen: str | None) -> None:
        g = self.guardado
        if modo is not None:
            if modo not in greeter.MODOS:
                raise ValueError(f"modo de greeter no valido: {modo!r} ({', '.join(greeter.MODOS)})")
            g.greeter_modo = modo
        if imagen is not None:
            ruta = Path(os.path.abspath(Path(imagen).expanduser()))
            if not ruta.is_file() or tipo_de(ruta) is None:
                raise ValueError(f"no es un fondo valido: {ruta}")
            g.greeter_imagen = str(ruta)
        g.escribir()
        self._greeter_copiado = None  # que se copie aunque coincida con la ultima
        log.info("greeter: modo %s%s", g.greeter_modo,
                 f" ({Path(g.greeter_imagen).name})" if g.greeter_modo == "fija" and g.greeter_imagen else "")
        await self._actualizar_greeter()

    async def refrescar_paleta(self) -> None:
        """Vuelve a mandar a Noctalia la imagen del monitor de la paleta.

        Hace falta al encender «colores segun el fondo» desde la ventana:
        Noctalia saca la paleta del ultimo wallpaper-set, y hasta el siguiente
        cambio de fondo no tendria de donde.
        """
        paleta = self.monitor_paleta()
        imagen = self.pantallas[paleta].imagen_noctalia if paleta else None
        if imagen:
            await backends.noctalia_set(imagen, paleta)
            log.info("paleta: refrescada desde %s", imagen.name)

    # -------------------------------------------------------- rotacion

    def _programar_rotacion(self) -> None:
        """Empieza la cuenta hasta el proximo cambio (o la para si esta apagada)."""
        if self._rotacion_timer:
            self._rotacion_timer.cancel()
            self._rotacion_timer = None
        if not self.guardado.rotacion:
            self._rotacion_hasta = None
            return
        loop = asyncio.get_running_loop()
        self._rotacion_hasta = loop.time() + self.minutos_rotacion * 60
        self._rotacion_timer = loop.call_at(self._rotacion_hasta, lambda: self._lanzar(self._toca_rotar()))

    def _cambio_manual(self, monitores) -> None:
        # Quien acaba de elegir un fondo no quiere que la rotacion se lo quite
        # al minuto: la cuenta vuelve a empezar
        self.pendientes -= set(monitores)
        if self.guardado.rotacion:
            self._programar_rotacion()

    @property
    def minutos_rotacion(self) -> float:
        return self.guardado.rotacion_minutos or self.cfg.rotation_minutes

    async def poner_rotacion(self, encendida: bool | None = None, minutos: float | None = None,
                             orden: str | None = None, excluir: list[str] | None = None,
                             incluir: list[str] | None = None) -> None:
        """Cambia cualquier ajuste de la rotacion; lo que llegue como None no se toca."""
        g = self.guardado
        cambios = []
        if minutos is not None:
            if not isinstance(minutos, (int, float)) or isinstance(minutos, bool) or not 0.1 <= minutos <= 7 * 24 * 60:
                raise ValueError(f"minutos de rotacion no validos: {minutos!r}")
            g.rotacion_minutos = float(minutos)
            cambios.append(f"cada {minutos:g} min")
        if orden is not None:
            if orden not in ("orden", "aleatorio"):
                raise ValueError(f"orden de rotacion no valido: {orden!r} (orden o aleatorio)")
            g.rotacion_orden = orden
            cambios.append(f"orden {orden}")
        for ruta in excluir or []:
            g.rotacion_excluidos.add(str(ruta))
        for ruta in incluir or []:
            g.rotacion_excluidos.discard(str(ruta))
        if excluir or incluir:
            cambios.append(f"{len(g.rotacion_excluidos)} fuera de la rotacion")
        if encendida is not None and encendida != g.rotacion:
            g.rotacion = encendida
            self.pendientes.clear()
            cambios.insert(0, f"encendida, cada {self.minutos_rotacion:g} min" if encendida else "apagada")
        g.escribir()
        # Un intervalo nuevo o encenderla empiezan la cuenta de cero
        if encendida is not None or minutos is not None:
            self._programar_rotacion()
        if cambios:
            log.info("rotacion: %s", "; ".join(cambios))

    async def _toca_rotar(self) -> None:
        self._programar_rotacion()  # la siguiente cuenta empieza ya
        self.pendientes |= set(self.pantallas)
        await self._rotar_pendientes()

    async def _rotar_pendientes(self) -> None:
        self._gracia_timer = None
        self.pendientes &= set(self.pantallas)  # fuera los monitores desconectados
        libres = [n for n in self.pendientes if n in self.pantallas and not self.pausado(self.pantallas[n])]
        esperando = sorted(set(self.pendientes) - set(libres))
        if esperando:
            log.info("rotacion: espera hasta que se quite la pausa en %s", ", ".join(esperando))
        if not libres:
            return
        asignaciones = self._siguientes(libres, rotacion=True)
        # Si lo unico que queda en la rotacion es lo que ya suena, no se toca:
        # volver a ponerlo reiniciaria el video para nada
        asignaciones = {n: f for n, f in asignaciones.items() if f != self.pantallas[n].actual}
        self.pendientes -= set(libres)
        if not asignaciones:
            log.info("rotacion: no hay ningun fondo en la rotacion; nada que cambiar")
            return
        log.info("rotacion: toca cambiar en %s", ", ".join(sorted(asignaciones)))
        try:
            async with self._lock:
                await self._aplicar(asignaciones, generales=self._repartir(asignaciones))
        except (BackendError, ValueError) as e:
            log.error("rotacion: no pude cambiar: %s", e)

    def _revisar_pendientes(self) -> None:
        """Tras cada cambio de pausa: si un monitor pendiente ya no esta en
        pausa, cambia cuando lleve GRACIA_ROTACION_S asi."""
        libre = any(n in self.pantallas and not self.pausado(self.pantallas[n]) for n in self.pendientes)
        if libre and self._gracia_timer is None:
            self._gracia_timer = asyncio.get_running_loop().call_later(
                GRACIA_ROTACION_S, lambda: self._lanzar(self._rotar_pendientes()))
        elif not libre and self._gracia_timer is not None:
            self._gracia_timer.cancel()
            self._gracia_timer = None

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
        # Las razones de pausa cuentan tambien con imagenes fijas: un cambio
        # de fondo recolorea GTK, Qt y kitty, y eso no se hace en mitad de un juego
        if self.pendientes:
            self._revisar_pendientes()
    async def _evaluar_pausa(self) -> None:
        try:
            await self._consultar_hyprland()
        except (OSError, HyprlandError) as e:
            log.warning("no pude consultar Hyprland: %s", e)
            return
        await self._sincronizar_pausa()
        await self._sincronizar_workspaces()

    async def _sincronizar_workspaces(self) -> None:
        """Pone en cada monitor el fondo del workspace visible (o el general).

        Solo hace algo si lo que se ve no es lo que toca, es decir, al pasar a
        un workspace con fondo propio o al salir de el. Sin fondos propios no
        cambia nada.
        """
        cambios = {}
        for nombre, p in self.pantallas.items():
            if p.actual is None:
                continue  # aun sin restaurar: de eso se encarga _restaurar
            deseado = self._deseado(nombre)
            if deseado and deseado != p.actual and deseado.is_file():
                cambios[nombre] = deseado
        if not cambios:
            return
        async with self._lock:
            # Puede haberse movido mientras se esperaba el cerrojo
            cambios = {n: f for n, f in cambios.items() if n in self.pantallas and f != self.pantallas[n].actual}
            if cambios:
                try:
                    await self._aplicar(cambios, generales=set())
                except (BackendError, ValueError) as e:
                    log.error("workspace: no pude cambiar el fondo: %s", e)
                    return
        for n in cambios:
            log.info("%s: workspace %s -> %s", n, self.pantallas[n].workspace,
                     "fondo propio" if self._propio(self.pantallas[n]) else "fondo general")

    async def quitar_propio(self, monitor: str | None) -> None:
        """El workspace visible vuelve a usar el fondo general del monitor."""
        quitados = []
        for nombre in self._destinos(monitor):
            ws = self.pantallas[nombre].workspace
            if ws and self.guardado.workspaces.pop(ws, None):
                quitados.append(ws)
        if not quitados:
            raise ValueError("el workspace visible no tiene fondo propio")
        self.guardado.escribir()
        log.info("workspace %s: vuelve al fondo general", ", ".join(quitados))
        await self._sincronizar_workspaces()

    async def _consultar_hyprland(self) -> None:
        completas: dict[str, str] = {}
        monitores = await self.hypr.query("monitors")
        for m in monitores:
            if m["name"] in self.pantallas:
                self.pantallas[m["name"]].workspace = m["activeWorkspace"]["name"]
        if self.cfg.pause_on_fullscreen:
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
            await self.set(Path(p["path"]), monitor, solo_workspace=bool(p.get("workspace")),
                           general=bool(p.get("general")))
        elif orden == "workspace-clear":
            await self.quitar_propio(monitor)
        elif orden == "next":
            await self.next(monitor)
        elif orden == "pause":
            self.manual_pause = self.guardado.pausa_manual = True
            self.guardado.escribir()
            log.info("pausa manual pedida")
            await self._sincronizar_pausa()
        elif orden == "resume":
            self.manual_pause = self.guardado.pausa_manual = False
            self.guardado.escribir()
            log.info("pausa manual retirada")
            await self._sincronizar_pausa()
        elif orden == "rotate":
            await self.poner_rotacion(
                encendida=p.get("enabled"), minutos=p.get("minutes"), orden=p.get("order"),
                excluir=p.get("exclude"), incluir=p.get("include"))
        elif orden == "greeter":
            await self.poner_greeter(p.get("mode"), p.get("image"))
        elif orden == "palette":
            await self.refrescar_paleta()
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
                "workspace": p.workspace,
                "workspace_own": bool(self._propio(p)),
            }
            if p.player and p.player.alive:
                info = await p.player.info()
                m.update(backend="mpvpaper", pid=p.player.proc.pid,
                         hwdec=info.get("hwdec-current"), mpv_paused=info.get("pause"))
            monitores.append(m)
        return {"monitors": monitores, "palette_monitor": paleta,
                "directory": str(self.cfg.directory),
                "workspaces": dict(self.guardado.workspaces),
                "manual_pause": self.manual_pause, "on_battery": self.bateria,
                "rotation": {
                    "enabled": self.guardado.rotacion,
                    "interval_minutes": self.minutos_rotacion,
                    "order": self.guardado.rotacion_orden,
                    "excluded": sorted(self.guardado.rotacion_excluidos),
                    "next_in_s": round(self._rotacion_hasta - asyncio.get_running_loop().time())
                    if self._rotacion_hasta else None,
                    "pending": sorted(self.pendientes),
                },
                "greeter": {
                    "available": greeter.disponible(),
                    "mode": self.guardado.greeter_modo,
                    "image": self.guardado.greeter_imagen,
                    "file": str(greeter.FICHERO),
                    "last": str(self._greeter_copiado) if self._greeter_copiado else None,
                }}


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
