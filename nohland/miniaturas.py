"""Miniaturas para la interfaz, hechas con ffmpeg y cacheadas en disco.

ffmpeg sirve igual para imagenes, videos y GIF, asi que no hace falta otra
biblioteca. Son funciones bloqueantes: la interfaz las llama desde hilos.
"""

import hashlib
import os
import subprocess
from pathlib import Path

from .config import CACHE_DIR

MINIATURAS_DIR = CACHE_DIR / "miniaturas"
# Tamanos que pide la interfaz: tarjetas de la coleccion y portada
TARJETA = (640, 360)
PORTADA = (1280, 720)


def _ruta_cache(path: Path, ancho: int, alto: int) -> Path:
    st = path.stat()
    clave = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
    return MINIATURAS_DIR / f"{clave}-{ancho}x{alto}.jpg"


def _duracion(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def miniatura(path: Path, animado: bool, tamano: tuple[int, int] = TARJETA) -> Path | None:
    """Devuelve la miniatura (creandola si falta) o None si ffmpeg no puede."""
    ancho, alto = tamano
    try:
        destino = _ruta_cache(path, ancho, alto)
    except OSError:
        return None
    if destino.exists():
        return destino
    MINIATURAS_DIR.mkdir(parents=True, exist_ok=True)

    # Igual que el fotograma de la paleta: al 25 %, para saltar fundidos a negro
    ss = _duracion(path) * 0.25 if animado else 0.0
    tmp = destino.with_suffix(f".{os.getpid()}.tmp.jpg")
    recorte = f"scale={ancho}:{alto}:force_original_aspect_ratio=increase,crop={ancho}:{alto}"
    for intento in dict.fromkeys((ss, 0.0)):
        # Nada de -ss en una imagen fija: con un JPEG, ffmpeg 9 no saca ningun
        # fotograma y aun asi termina con codigo 0
        salto = ["-ss", f"{intento:.2f}"] if animado else []
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *salto, "-i", str(path),
             "-frames:v", "1", "-update", "1", "-vf", recorte, "-q:v", "3", str(tmp)],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size:
            os.replace(tmp, destino)
            return destino
    tmp.unlink(missing_ok=True)
    return None
