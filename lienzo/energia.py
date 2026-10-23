"""¿Estamos con bateria? Lectura de /sys y aviso del kernel cuando cambia.

Sin dependencias ni sondeos: el kernel manda un uevent por netlink cada vez que
cambia una fuente de energia (enchufar, desenchufar, cada % de bateria) y solo
entonces se relee /sys/class/power_supply. Escuchar los uevents del kernel no
necesita root.

En la torre no hay ninguna fuente de energia en /sys, asi que en_bateria()
devuelve None y la pausa por bateria nunca salta.
"""

import asyncio
import os
import socket
from collections.abc import AsyncIterator
from pathlib import Path

# Se puede apuntar a otra carpeta para probar sin desenchufar nada
POWER_DIR = Path(os.environ.get("LIENZO_POWER_SUPPLY_DIR", "/sys/class/power_supply"))

NETLINK_KOBJECT_UEVENT = 15  # linux/netlink.h; Python no exporta la constante
GRUPO_UEVENTS_KERNEL = 1

# Tipos de cargador. Los USB-C del portatil aparecen como "USB" con online=1.
TIPOS_CARGADOR = {"Mains", "USB", "USB_C", "USB_PD", "USB_PD_DRP", "Wireless"}


def _leer(f: Path) -> str:
    try:
        return f.read_text().strip()
    except OSError:
        return ""


def en_bateria(base: Path = POWER_DIR) -> bool | None:
    """True con bateria, False enchufado, None si el equipo no tiene bateria."""
    baterias, cargadores = [], []
    try:
        fuentes = list(base.iterdir())
    except OSError:
        return None
    for d in fuentes:
        tipo = _leer(d / "type")
        # scope=Device son baterias de perifericos (raton, mando): no cuentan
        if tipo == "Battery" and _leer(d / "scope") != "Device":
            baterias.append(d)
        elif tipo in TIPOS_CARGADOR:
            cargadores.append(d)

    if not baterias:
        return None
    if cargadores:
        return not any(_leer(c / "online") == "1" for c in cargadores)
    # Sin cargador visible en /sys, lo dice la propia bateria
    return any(_leer(b / "status") == "Discharging" for b in baterias)


async def cambios() -> AsyncIterator[None]:
    """Produce un valor cada vez que el kernel avisa de un cambio de energia."""
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
    sock.bind((0, GRUPO_UEVENTS_KERNEL))
    sock.setblocking(False)
    loop = asyncio.get_running_loop()
    try:
        while True:
            datos = await loop.sock_recv(sock, 1 << 16)
            # Llegan uevents de todos los subsistemas; solo interesan estos
            if b"SUBSYSTEM=power_supply" in datos:
                yield
    finally:
        sock.close()
