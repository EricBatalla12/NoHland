"""Cliente minimo de los dos sockets de Hyprland.

  .socket.sock   peticiones ("j/monitors" -> JSON). Hyprland cierra la
                 conexion tras responder, asi que se lee hasta EOF.
  .socket2.sock  flujo de eventos "nombre>>datos", una linea por evento.

Se habla con los sockets directamente en vez de lanzar hyprctl: no cuesta un
proceso por consulta y el daemon puede quedarse dormido en el read() del
socket de eventos sin gastar CPU.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path


class HyprlandError(Exception):
    pass


class Hyprland:
    def __init__(self) -> None:
        firma = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if not firma:
            raise HyprlandError("no hay HYPRLAND_INSTANCE_SIGNATURE: ¿se esta ejecutando fuera de Hyprland?")
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        base = runtime / "hypr" / firma
        self.request_path = base / ".socket.sock"
        self.events_path = base / ".socket2.sock"
        if not self.events_path.exists():
            raise HyprlandError(f"no existe {self.events_path}")

    async def query(self, comando: str):
        """Lanza una peticion JSON, p. ej. query("monitors")."""
        reader, writer = await asyncio.open_unix_connection(self.request_path)
        try:
            writer.write(f"j/{comando}".encode())
            await writer.drain()
            datos = await reader.read()
        finally:
            writer.close()
        try:
            return json.loads(datos)
        except json.JSONDecodeError as e:
            raise HyprlandError(f"respuesta no JSON a {comando!r}: {datos[:120]!r}") from e

    async def events(self) -> AsyncIterator[tuple[str, str]]:
        # El writer hay que conservarlo aunque no se escriba nada: si se
        # descarta, el recolector lo destruye y cierra el socket por debajo,
        # y el siguiente readline() devuelve EOF.
        reader, writer = await asyncio.open_unix_connection(self.events_path, limit=1 << 20)
        try:
            while linea := await reader.readline():
                nombre, _, datos = linea.decode(errors="replace").rstrip("\n").partition(">>")
                yield nombre, datos
        finally:
            writer.close()
        raise HyprlandError("Hyprland ha cerrado el socket de eventos")
