"""Pasar los fondos a otro equipo con un .zip.

Exportar guarda los fondos de las carpetas y lo que se elige en la ventana
(fondo general, fondo propio de cada workspace, excluidos de la rotacion) en
un .zip; importarlo en el otro equipo copia los fondos a su carpeta y le
aplica esas elecciones. Las rutas van por nombre de fichero: la carpeta (y el
usuario) cambian de un equipo a otro.

Los fondos de Wallpaper Engine (enlaces a steamapps/workshop) no se pasan: son
de la cuenta de Steam de cada uno y pesan mucho; el otro equipo los tiene si
esta suscrito.

Los medios van sin comprimir (ZIP_STORED): JPG, PNG y MP4 ya estan
comprimidos y deflate solo gastaria CPU para ganar un 1 o 2 %.
"""

import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import ANIMATED_EXT, IMAGE_EXT, Config

MANIFIESTO = "nohland.json"
CARPETA_ZIP = "fondos/"
VERSION = 1


@dataclass
class Contenido:
    fondos: list[Path] = field(default_factory=list)
    steam: list[Path] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(p.stat().st_size for p in self.fondos)


def de_steam(path: Path) -> bool:
    return "steamapps" in path.resolve().parts


def contenido(cfg: Config) -> Contenido:
    """Los fondos que entrarian en el paquete y los de Steam que se quedan fuera."""
    c, vistos = Contenido(), set()
    for carpeta in [cfg.directory, *cfg.monitor_dirs.values()]:
        if not carpeta.is_dir():
            continue
        for p in sorted(carpeta.iterdir(), key=lambda p: p.name.lower()):
            # Dos carpetas con un fichero del mismo nombre: entra el primero
            if not p.is_file() or p.suffix.lower() not in IMAGE_EXT | ANIMATED_EXT or p.name in vistos:
                continue
            vistos.add(p.name)
            (c.steam if de_steam(p) else c.fondos).append(p)
    return c


def exportar(cfg: Config, guardado, destino: Path) -> Contenido:
    """Escribe el .zip. guardado es el daemon.Guardado leido de state.json."""
    c = contenido(cfg)
    nombres = {str(p): p.name for p in c.fondos}

    def nombre(ruta) -> str | None:
        return nombres.get(str(ruta)) if ruta else None

    manifiesto = {
        "version": VERSION,
        "general": nombre(guardado.ultimo),
        "workspaces": {ws: n for ws, r in guardado.workspaces.items() if (n := nombre(r))},
        "rotation_excluded": sorted(n for r in guardado.rotacion_excluidos if (n := nombre(r))),
    }
    tmp = destino.with_name(destino.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(MANIFIESTO, json.dumps(manifiesto, ensure_ascii=False, indent=1))
        for p in c.fondos:
            zf.write(p, CARPETA_ZIP + p.name)
    tmp.replace(destino)
    return c


def importar(origen: Path, carpeta: Path) -> tuple[dict, dict[str, Path]]:
    """Copia los fondos del .zip a carpeta. Devuelve el manifiesto y donde ha
    quedado cada nombre. Un fichero que ya esta con el mismo tamaño se da por
    el mismo; si es otro con el mismo nombre, el nuevo va como «nombre (2)»."""
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas: dict[str, Path] = {}
    with zipfile.ZipFile(origen) as zf:
        try:
            manifiesto = json.loads(zf.read(MANIFIESTO))
        except KeyError:
            raise ValueError(f"{origen.name} no es un paquete de NoHland (falta {MANIFIESTO})") from None
        for info in zf.infolist():
            if info.is_dir() or not info.filename.startswith(CARPETA_ZIP):
                continue
            # Solo el nombre: un zip con «../» no puede escribir fuera de la carpeta
            nombre = Path(info.filename).name
            if not nombre or Path(nombre).suffix.lower() not in IMAGE_EXT | ANIMATED_EXT:
                continue
            destino = carpeta / nombre
            n = 2
            while destino.exists() and destino.stat().st_size != info.file_size:
                destino = carpeta / f"{Path(nombre).stem} ({n}){Path(nombre).suffix}"
                n += 1
            if not destino.exists():
                tmp = destino.with_name(destino.name + ".part")
                with zf.open(info) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                tmp.replace(destino)
            rutas[nombre] = destino
    return manifiesto, rutas


def tamano(n: int) -> str:
    for unidad in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unidad}"
        n /= 1024
    return f"{n:.1f} GB"
