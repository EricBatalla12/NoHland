"""Rutas y lectura de ~/.config/lienzo/config.toml.

Todo tiene valor por defecto: sin fichero de config, lienzo arranca igual.
Los errores de config (tipos o expresiones regulares mal escritas) paran el
daemon con un mensaje claro en vez de ignorarse en silencio.
"""

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "lienzo" / "config.toml"
STATE_PATH = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "lienzo" / "state.json"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "lienzo"
CONTROL_SOCKET = RUNTIME_DIR / "control.sock"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "lienzo"

# Formatos que Noctalia sabe dibujar (los de su documentacion, salvo GIF, que
# va a mpvpaper para que se anime).
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".jxl", ".bmp", ".avif", ".heic", ".heif"}
ANIMATED_EXT = {".mp4", ".webm", ".gif"}

DEFAULT_DIRECTORY = "~/Imágenes/lienzo"

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
    # Carpeta propia de algun monitor para `lienzo next`
    monitor_dirs: dict[str, Path] = field(default_factory=dict)
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
