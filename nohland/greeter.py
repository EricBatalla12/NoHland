"""Fondo de la pantalla de inicio de sesion (noctalia-greeter).

El greeter corre como el usuario «greeter» y no puede leer tu home, y su
carpeta (/var/lib/noctalia-greeter) es de root. El punto de encuentro es
/var/lib/greeter-wallpaper: tuya para escribir, legible por el greeter, y el
greeter configurado para leer de ahi current.png. Se prepara una sola vez con
`sudo tools/preparar-greeter.sh`; despues NoHland solo copia un fichero en
cada cambio, sin contraseña y sin coste apreciable.

La extension no importa: el fichero se llama current.png aunque sea un JPG,
porque Noctalia reconoce el formato por el contenido (comprobado con
`noctalia theme` sobre un JPG renombrado).
"""

import os
import shutil
from pathlib import Path

CARPETA = Path(os.environ.get("NOHLAND_GREETER_DIR", "/var/lib/greeter-wallpaper"))
FICHERO = CARPETA / "current.png"

MODOS = ("escritorio", "fija", "no")


def disponible() -> bool:
    """Si la carpeta compartida existe y se puede escribir en ella."""
    return CARPETA.is_dir() and os.access(CARPETA, os.W_OK)


def copiar(origen: Path) -> None:
    """Copia atomica: el greeter nunca ve un fichero a medio escribir."""
    tmp = CARPETA / ".current.tmp"
    shutil.copyfile(origen, tmp)
    tmp.chmod(0o644)
    os.replace(tmp, FICHERO)
