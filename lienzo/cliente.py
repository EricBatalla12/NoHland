"""Cliente del socket de control del daemon. Lo usan el CLI y la interfaz."""

import json
import socket

from .config import CONTROL_SOCKET


class DaemonParado(Exception):
    pass


def enviar(peticion: dict, timeout: float = 30) -> dict:
    """Manda una orden y devuelve la respuesta ({"ok": ..., ...})."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(CONTROL_SOCKET))
            s.sendall(json.dumps(peticion).encode() + b"\n")
            datos = b""
            while trozo := s.recv(65536):
                datos += trozo
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise DaemonParado("el daemon no esta en marcha (systemctl --user start lienzo)") from e
    except TimeoutError as e:
        raise DaemonParado(f"el daemon no ha respondido en {timeout:.0f} s") from e
    return json.loads(datos)
