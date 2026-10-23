"""Buscar e instalar versiones nuevas de NoHland desde su repo de GitHub.

NoHland se instala con enlaces a una copia de git del repo, asi que actualizar
es `git pull` + `install.sh` + reiniciar el servicio. Esto lo hace la ventana
al pulsar «Actualizar»; nunca solo.

Solo se ofrece actualizar cuando la copia local esta POR DETRAS de GitHub y
no tiene commits propios: en el equipo donde se desarrolla, la copia va por
delante y el aviso no sale. Con cambios sin guardar en la copia, se avisa pero
no se toca nada (`git pull --ff-only` fallaria o los pisaria).

Funciones bloqueantes: la ventana las llama desde hilos.
"""

import json
import os
import subprocess
import time
from pathlib import Path

from .config import CACHE_DIR

# La copia del repo de la que se ejecuta NoHland (bin/nohland es un enlace)
REPO = Path(__file__).resolve().parent.parent
CACHE = CACHE_DIR / "actualizaciones.json"
# Al abrir la ventana se mira como mucho cada tanto; «Buscar actualizaciones»
# del menu fuerza la consulta
INTERVALO_S = 6 * 3600


def _git(*args: str, timeout: int = 30) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"git {args[0]} fallo")
    return r.stdout.strip()


def comprobar(forzar: bool = False) -> dict:
    """Compara la copia local con GitHub.

    Devuelve {"estado": ..., "nuevos": [[sha, titulo], ...], "detalle": ...}:
      al-dia            nada nuevo
      nueva             hay commits nuevos y se puede actualizar
      cambios-locales   hay commits nuevos, pero la copia tiene cambios sin guardar
      por-delante       la copia tiene commits que GitHub no tiene (desarrollo)
      no-git            NoHland no esta instalado desde una copia de git
      error             no se pudo consultar (sin red, etc.)
    """
    try:
        cabeza = _git("rev-parse", "HEAD")
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        cabeza = None
    if not forzar:
        try:
            guardado = json.loads(CACHE.read_text())
            # La cache solo vale para la misma version local: si se actualizo a
            # mano entre medias, el aviso guardado ya no es cierto
            if time.time() - guardado["cuando"] < INTERVALO_S and guardado.get("cabeza") == cabeza:
                return guardado["resultado"]
        except (OSError, ValueError, KeyError):
            pass

    if not (REPO / ".git").exists():
        resultado = {"estado": "no-git", "nuevos": [], "detalle": str(REPO)}
    else:
        try:
            _git("fetch", "--quiet", "origin", timeout=30)
            detras = int(_git("rev-list", "--count", "HEAD..@{u}"))
            delante = int(_git("rev-list", "--count", "@{u}..HEAD"))
            sucio = bool(_git("status", "--porcelain", "--untracked-files=no"))
            nuevos = [linea.split("\t", 1) for linea in
                      _git("log", "--format=%h\t%s", "HEAD..@{u}").splitlines() if linea]
            if delante:
                estado = "por-delante"
            elif not detras:
                estado = "al-dia"
            elif sucio:
                estado = "cambios-locales"
            else:
                estado = "nueva"
            resultado = {"estado": estado, "nuevos": nuevos,
                         "detalle": f"{detras} detrás, {delante} por delante"}
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as e:
            resultado = {"estado": "error", "nuevos": [], "detalle": str(e)}

    if resultado["estado"] != "error":
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"cuando": time.time(), "cabeza": cabeza, "resultado": resultado},
                                    ensure_ascii=False))
    return resultado


def actualizar(reiniciar: bool = True) -> str:
    """git pull + install.sh + reiniciar el servicio. Devuelve un resumen."""
    antes = _git("rev-parse", "--short", "HEAD")
    _git("pull", "--ff-only", "--quiet", timeout=120)
    despues = _git("rev-parse", "--short", "HEAD")
    r = subprocess.run(["bash", str(REPO / "install.sh")], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "NOHLAND_SIN_DOCTOR": "1"})
    if r.returncode != 0:
        raise RuntimeError(f"install.sh fallo: {(r.stdout + r.stderr).strip()[-400:]}")
    if reiniciar:
        subprocess.run(["systemctl", "--user", "restart", "nohland"], capture_output=True, timeout=30)
    CACHE.unlink(missing_ok=True)
    return f"{antes} → {despues}"
