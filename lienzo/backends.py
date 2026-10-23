"""Quien dibuja el fondo.

  Imagenes fijas -> Noctalia (`noctalia msg wallpaper-set`). Noctalia ya pinta
                    el fondo con transicion en la capa background y, al
                    cambiarlo, regenera la paleta. No hace falta otro daemon.
  Video y GIF    -> mpvpaper en la capa bottom, por encima del fondo de
                    Noctalia y por debajo de las ventanas. Se controla por el
                    socket IPC de mpv (input-ipc-server), asi que pausar no
                    mata el proceso: el ultimo fotograma se queda en pantalla.
"""

import asyncio
import itertools
import json
import logging
from pathlib import Path

from .config import RUNTIME_DIR, Config

log = logging.getLogger("lienzo")


class BackendError(Exception):
    pass


# ------------------------------------------------------------ Noctalia

async def noctalia_set(path: Path, intentos: int = 1) -> None:
    """Pone una imagen como fondo de Noctalia.

    Con intentos > 1 reintenta cada segundo: al arrancar la sesion el servicio
    puede llegar antes que Noctalia, que se lanza desde el autostart.
    """
    ultimo = ""
    for i in range(intentos):
        if i:
            await asyncio.sleep(1)
        proc = await asyncio.create_subprocess_exec(
            "noctalia", "msg", "wallpaper-set", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        salida, _ = await proc.communicate()
        if proc.returncode == 0:
            return
        ultimo = salida.decode(errors="replace").strip()
    raise BackendError(f"noctalia msg wallpaper-set fallo: {ultimo or 'sin salida'}")


# ------------------------------------------------------------ mpvpaper

_contador = itertools.count(1)


class MpvPaper:
    """Un proceso mpvpaper reproduciendo un fichero en todas las salidas."""

    def __init__(self, cfg: Config, path: Path) -> None:
        self.cfg = cfg
        self.path = path
        # Socket distinto por instancia: al cambiar de video se arranca el
        # nuevo antes de cerrar el viejo y no pueden pisarse.
        self.ipc_path = RUNTIME_DIR / f"mpv-{next(_contador)}.sock"
        self.proc: asyncio.subprocess.Process | None = None
        self.paused = False
        self._log_task: asyncio.Task | None = None

    def _mpv_options(self, paused: bool) -> str:
        opciones = [
            "no-audio",
            # Sin la linea de estado "V: 00:00:01 / ..." que mpv escribe varias
            # veces por segundo y llenaria el journal
            "quiet",
            "loop-file=inf",
            f"hwdec={self.cfg.hwdec}",
            # Recorta para llenar la pantalla en vez de dejar franjas negras
            "panscan=1.0",
            f"input-ipc-server={self.ipc_path}",
        ]
        if paused:
            opciones.append("pause")
        # mpvpaper separa las opciones por espacios
        return " ".join(opciones + self.cfg.mpv_options)

    async def start(self, paused: bool) -> None:
        self.ipc_path.unlink(missing_ok=True)
        self.proc = await asyncio.create_subprocess_exec(
            "mpvpaper", "-l", self.cfg.layer, "-o", self._mpv_options(paused),
            "ALL", str(self.path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        self.paused = paused
        self._log_task = asyncio.create_task(self._reenviar_salida())

        # Se da por arrancado cuando responde por IPC: hasta entonces no se
        # puede pausar ni preguntar por el decodificador.
        for _ in range(100):
            if self.proc.returncode is not None:
                raise BackendError(f"mpvpaper termino al arrancar (codigo {self.proc.returncode})")
            if self.ipc_path.exists():
                try:
                    await self.command("get_property", "pid")
                    return
                except (OSError, BackendError):
                    pass
            await asyncio.sleep(0.1)
        await self.stop()
        raise BackendError("mpvpaper no abrio su socket IPC en 10 s")

    async def _reenviar_salida(self) -> None:
        """Pasa lo que escriba mpvpaper al journal, con prefijo."""
        assert self.proc and self.proc.stdout
        while linea := await self.proc.stdout.readline():
            texto = linea.decode(errors="replace").rstrip()
            if texto:
                log.info("mpvpaper[%d]: %s", self.proc.pid, texto)

    async def command(self, *args):
        """Manda un comando a mpv por su socket JSON y devuelve el campo data."""
        reader, writer = await asyncio.open_unix_connection(self.ipc_path)
        try:
            writer.write(json.dumps({"command": list(args), "request_id": 1}).encode() + b"\n")
            await writer.drain()
            # mpv intercala eventos asincronos; la respuesta es la que lleva
            # nuestro request_id
            while linea := await asyncio.wait_for(reader.readline(), 2):
                msg = json.loads(linea)
                if msg.get("request_id") == 1:
                    if msg.get("error") != "success":
                        raise BackendError(f"mpv {args}: {msg.get('error')}")
                    return msg.get("data")
            raise BackendError("mpv cerro el socket sin responder")
        finally:
            writer.close()

    async def set_pause(self, paused: bool) -> None:
        await self.command("set_property", "pause", paused)
        self.paused = paused

    async def info(self) -> dict:
        """Datos para `lienzo status`; vacio si mpv no contesta."""
        datos = {}
        for prop in ("hwdec-current", "pause"):
            try:
                datos[prop] = await self.command("get_property", prop)
            except (OSError, BackendError, asyncio.TimeoutError):
                pass
        return datos

    async def wait(self) -> int:
        assert self.proc
        return await self.proc.wait()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def stop(self) -> None:
        if self.alive:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                log.warning("mpvpaper[%d] no respondio a SIGTERM; se mata", self.proc.pid)
                self.proc.kill()
                await self.proc.wait()
        if self._log_task:
            await asyncio.gather(self._log_task, return_exceptions=True)
        self.ipc_path.unlink(missing_ok=True)
