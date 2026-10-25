"""Quien dibuja el fondo.

  Imagenes fijas -> Noctalia (`noctalia msg wallpaper-set`). Noctalia ya pinta
                    el fondo con transicion en la capa background y, al
                    cambiarlo, regenera la paleta. No hace falta otro daemon.
  Video y GIF    -> mpvpaper en la capa bottom, por encima del fondo de
                    Noctalia y por debajo de las ventanas. Se controla por el
                    socket IPC de mpv (input-ipc-server), asi que pausar no
                    mata el proceso: el ultimo fotograma se queda en pantalla.

Con un video, Noctalia recibe ademas un fotograma suyo sacado con ffmpeg: queda
dibujado debajo de mpvpaper (lo ve la pantalla de bloqueo y lo que aparece si
mpvpaper se cierra) y de el sale la paleta.

Paleta: Noctalia la genera desde el ULTIMO `wallpaper-set` que recibe, sea del
monitor que sea y aunque repita la misma ruta (comprobado con dos monitores).
Por eso el daemon deja siempre para el final el monitor que manda en la paleta.
"""

import asyncio
import hashlib
import itertools
import json
import logging
import os
from pathlib import Path

from .config import CACHE_DIR, RUNTIME_DIR, Config

log = logging.getLogger("nohland")


class BackendError(Exception):
    pass


# ------------------------------------------------------------ Noctalia

async def noctalia_set(path: Path, monitor: str | None = None, intentos: int = 1) -> None:
    """Pone una imagen como fondo de Noctalia en un monitor (o en todos).

    Con intentos > 1 reintenta cada segundo: al arrancar la sesion el servicio
    puede llegar antes que Noctalia, que se lanza desde el autostart.
    """
    ultimo = ""
    for i in range(intentos):
        if i:
            await asyncio.sleep(1)
        proc = await asyncio.create_subprocess_exec(
            "noctalia", "msg", "wallpaper-set", *([monitor] if monitor else []), str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        salida, _ = await proc.communicate()
        if proc.returncode == 0:
            return
        ultimo = salida.decode(errors="replace").strip()
    raise BackendError(f"noctalia msg wallpaper-set fallo: {ultimo or 'sin salida'}")


async def noctalia_get(monitor: str) -> str | None:
    """La imagen que Noctalia tiene de verdad en ese monitor."""
    proc = await asyncio.create_subprocess_exec(
        "noctalia", "msg", "wallpaper-get", monitor,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    salida, _ = await proc.communicate()
    return salida.decode(errors="replace").strip() or None if proc.returncode == 0 else None


# ------------------------------------------------------------ fotograma

FRAMES_DIR = CACHE_DIR / "frames"


async def _salida(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    salida, _ = await proc.communicate()
    return proc.returncode, salida.decode(errors="replace").strip()


def ruta_frame(video: Path) -> Path:
    st = video.stat()
    clave = hashlib.sha1(f"{video}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:12]
    return FRAMES_DIR / f"{video.stem[:40]}-{clave}.jpg"


async def extraer_frame(video: Path) -> Path:
    """Saca un fotograma del video para Noctalia y lo cachea.

    El nombre lleva un hash de ruta, tamano y fecha: Noctalia cachea la textura
    por ruta, asi que si el video cambia el fotograma tiene que cambiar de
    nombre. Repetir el mismo video reutiliza el fichero sin llamar a ffmpeg.
    """
    destino = ruta_frame(video)
    if destino.exists():
        return destino
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    # Al 25 % de la duracion: el principio de muchos fondos es un fundido
    # desde negro y daria una paleta apagada.
    rc, dur = await _salida("ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(video))
    try:
        momento = float(dur) * 0.25 if rc == 0 else 0.0
    except ValueError:
        momento = 0.0

    tmp = destino.with_suffix(".tmp.jpg")
    for ss in dict.fromkeys((momento, 0.0)):  # si falla en ese punto, el primero
        rc, err = await _salida("ffmpeg", "-v", "error", "-y", "-ss", f"{ss:.2f}", "-i", str(video),
                                "-frames:v", "1", "-update", "1", "-q:v", "2", str(tmp))
        if rc == 0 and tmp.exists() and tmp.stat().st_size:
            os.replace(tmp, destino)
            return destino
    tmp.unlink(missing_ok=True)
    raise BackendError(f"ffmpeg no pudo sacar un fotograma de {video.name}: {err or 'sin salida'}")


def limpiar_frames(en_uso: set[Path]) -> None:
    """Borra los fotogramas que no estan en en_uso.

    El daemon pasa los de todos los videos de las carpetas, no solo los que
    suenan: con la rotacion se vuelve a ellos, y un 4K a 60 fps cuesta ~4 s
    de CPU sacarlo otra vez."""
    if not FRAMES_DIR.is_dir():
        return
    for f in FRAMES_DIR.iterdir():
        if f not in en_uso:
            f.unlink(missing_ok=True)


# ------------------------------------------------------------ mpvpaper

_contador = itertools.count(1)


class MpvPaper:
    """Un proceso mpvpaper reproduciendo un fichero en un monitor."""

    def __init__(self, cfg: Config, path: Path, monitor: str) -> None:
        self.cfg = cfg
        self.path = path
        self.monitor = monitor
        # Socket distinto por instancia: al cambiar de video se arranca el
        # nuevo antes de cerrar el viejo y no pueden pisarse.
        self.ipc_path = RUNTIME_DIR / f"mpv-{monitor}-{next(_contador)}.sock"
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
            self.monitor, str(self.path),
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
            # Se cierra la escritura y se lee hasta que mpv cierre. Si se corta
            # antes, quedan sin leer los eventos que mpv manda a cada cliente
            # (file-loaded...) y el cierre llega como RST: mpv lo registra como
            # "Read error (Connection reset by peer)".
            writer.write_eof()
            respuesta = None
            async with asyncio.timeout(2):
                while linea := await reader.readline():
                    msg = json.loads(linea)
                    if msg.get("request_id") == 1:
                        respuesta = msg
        finally:
            writer.close()
        if respuesta is None:
            raise BackendError("mpv cerro el socket sin responder")
        if respuesta.get("error") != "success":
            raise BackendError(f"mpv {args}: {respuesta.get('error')}")
        return respuesta.get("data")

    async def set_pause(self, paused: bool) -> None:
        await self.command("set_property", "pause", paused)
        self.paused = paused

    async def info(self) -> dict:
        """Datos para `nohland status`; vacio si mpv no contesta."""
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
