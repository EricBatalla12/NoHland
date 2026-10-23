"""Rutas y lectura de ~/.config/nohland/config.toml.

Todo tiene valor por defecto: sin fichero de config, nohland arranca igual.
Los errores de config (tipos o expresiones regulares mal escritas) paran el
daemon con un mensaje claro en vez de ignorarse en silencio.
"""

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "nohland" / "config.toml"
STATE_PATH = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "nohland" / "state.json"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "nohland"
CONTROL_SOCKET = RUNTIME_DIR / "control.sock"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "nohland"

# Formatos que Noctalia sabe dibujar (los de su documentacion, salvo GIF, que
# va a mpvpaper para que se anime).
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".jxl", ".bmp", ".avif", ".heic", ".heif"}
ANIMATED_EXT = {".mp4", ".webm", ".gif"}

DEFAULT_DIRECTORY = "~/Imágenes/NoHland"

# Se comparan con re.search contra la clase y la clase inicial de la ventana
# activa, sin distinguir mayusculas.
DEFAULT_PAUSE_CLASSES = [
    r"^steam$",               # cliente de Steam, tambien Big Picture
    r"^steam_app_\d+$",       # juegos lanzados desde Steam, Proton incluido
    r"^(net\.lutris\.)?lutris$",
    r"^heroic$",
]


class ConfigError(Exception):
    pass


@dataclass
class Config:
    directory: Path = field(default_factory=lambda: Path(DEFAULT_DIRECTORY).expanduser())
    hwdec: str = "nvdec"
    layer: str = "bottom"
    mpv_options: list[str] = field(default_factory=list)
    pause_on_fullscreen: bool = True
    pause_on_battery: bool = True
    # Monitor del que sale la paleta; "" = el de id mas bajo en Hyprland
    palette_monitor: str = ""
    # Carpeta propia de algun monitor para `nohland next`
    monitor_dirs: dict[str, Path] = field(default_factory=dict)
    # Minutos entre cambios de la rotacion automatica. Si esta encendida o no
    # lo decide el interruptor de la ventana (se guarda en state.json)
    rotation_minutes: float = 15
    pause_classes: list[re.Pattern] = field(
        default_factory=lambda: [re.compile(p, re.IGNORECASE) for p in DEFAULT_PAUSE_CLASSES]
    )


def _get(table: dict, key: str, tipo: type, default, donde: str):
    valor = table.get(key, default)
    if not isinstance(valor, tipo):
        raise ConfigError(f"{donde}.{key} deberia ser {tipo.__name__}, no {type(valor).__name__}")
    return valor


def load(path: Path = CONFIG_PATH) -> tuple[Config, bool]:
    """Devuelve la config y si se ha leido de fichero (False = por defecto)."""
    if not path.exists():
        return Config(), False

    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e

    cfg = Config()
    cfg.directory = Path(_get(raw, "directory", str, DEFAULT_DIRECTORY, "")).expanduser()
    cfg.palette_monitor = _get(raw, "palette_monitor", str, "", "")

    for nombre, tabla in _get(raw, "monitors", dict, {}, "").items():
        if not isinstance(tabla, dict):
            raise ConfigError(f"monitors.{nombre} deberia ser una tabla")
        if "directory" in tabla:
            cfg.monitor_dirs[nombre] = Path(_get(tabla, "directory", str, "", f"monitors.{nombre}")).expanduser()

    video = _get(raw, "video", dict, {}, "")
    cfg.hwdec = _get(video, "hwdec", str, cfg.hwdec, "video")
    cfg.layer = _get(video, "layer", str, cfg.layer, "video")
    if cfg.layer not in ("background", "bottom", "top", "overlay"):
        raise ConfigError(f"video.layer = {cfg.layer!r} no es una capa valida")
    cfg.mpv_options = _get(video, "mpv_options", list, [], "video")
    for opcion in cfg.mpv_options:
        # mpvpaper recibe todas las opciones en una cadena y la corta por espacios
        if not isinstance(opcion, str) or not opcion or any(c.isspace() for c in opcion):
            raise ConfigError(f"video.mpv_options: {opcion!r} no vale (mpvpaper no admite espacios dentro de una opcion)")

    rotacion = _get(raw, "rotation", dict, {}, "")
    minutos = rotacion.get("interval_minutes", cfg.rotation_minutes)
    if isinstance(minutos, bool) or not isinstance(minutos, (int, float)) or minutos < 0.1:
        raise ConfigError(f"rotation.interval_minutes = {minutos!r}: tiene que ser un numero de minutos >= 0.1")
    cfg.rotation_minutes = float(minutos)

    pause = _get(raw, "pause", dict, {}, "")
    cfg.pause_on_fullscreen = _get(pause, "fullscreen", bool, True, "pause")
    cfg.pause_on_battery = _get(pause, "battery", bool, True, "pause")
    patrones = _get(pause, "classes", list, DEFAULT_PAUSE_CLASSES, "pause")
    try:
        cfg.pause_classes = [re.compile(p, re.IGNORECASE) for p in patrones]
    except re.error as e:
        raise ConfigError(f"pause.classes: expresion regular invalida ({e})") from e

    return cfg, True


def directorio_de(cfg: Config, monitor: str) -> Path:
    return cfg.monitor_dirs.get(monitor, cfg.directory)


# ------------------------------------------------------------ escritura
# La usa la ventana de ajustes. tomllib solo lee, asi que aqui hay un
# serializador minimo para el esquema de nohland (cadenas, booleanos, listas de
# cadenas y tablas). Los comentarios del fichero se pierden al guardar.

CABECERA = """\
# Configuracion de nohland, guardada desde la ventana de ajustes (nohland gui).
# La explicacion de cada clave esta en config.example.toml del repo.
"""


def leer_crudo(path: Path = CONFIG_PATH) -> dict:
    """El TOML tal cual, sin valores por defecto ({} si no existe)."""
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _clave(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k, ensure_ascii=False)


def _valor(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        # Los escapes de JSON (\\ \" \n \uXXXX) son validos en las cadenas
        # basicas de TOML; asi las regex con \d sobreviven intactas
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[\n" + "".join(f"    {_valor(x)},\n" for x in v) + "]"
    raise TypeError(f"no se puede guardar en TOML: {type(v).__name__}")


def _volcar(tabla: dict, prefijo: str, lineas: list[str]) -> None:
    simples = {k: v for k, v in tabla.items() if not isinstance(v, dict)}
    if prefijo and (simples or not any(isinstance(v, dict) for v in tabla.values())):
        lineas.append(f"\n[{prefijo}]")
    for k, v in simples.items():
        lineas.append(f"{_clave(k)} = {_valor(v)}")
    for k, v in tabla.items():
        if isinstance(v, dict):
            _volcar(v, f"{prefijo}.{_clave(k)}" if prefijo else _clave(k), lineas)


def escribir(raw: dict, path: Path = CONFIG_PATH) -> None:
    """Guarda la config. Antes de sustituir el fichero comprueba que se lee
    bien: si no, lanza ConfigError y el fichero viejo queda intacto."""
    lineas = [CABECERA.rstrip("\n")]
    _volcar(raw, "", lineas)
    texto = "\n".join(lineas) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(texto, encoding="utf-8")
    try:
        load(tmp)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
