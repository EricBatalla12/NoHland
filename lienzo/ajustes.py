"""Ventana de ajustes: edita ~/.config/lienzo/config.toml.

Los cambios no se aplican al momento: se guardan con el boton «Guardar y
aplicar» de cualquier pagina. Asi una regex a medio escribir no llega nunca al
daemon. Al guardar, config.escribir() valida el fichero antes de sustituirlo
y luego se reinicia el servicio para que lo lea.

Las claves que la ventana no conoce se conservan; los comentarios no.
"""

import copy
import shlex
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import config  # noqa: E402

DECODIFICADORES = [
    ("nvdec", "nvdec — NVIDIA, recomendado"),
    ("vaapi", "vaapi"),
    ("vulkan", "vulkan"),
    ("auto", "auto — que elija mpv"),
    ("no", "no — por CPU"),
]
CAPAS = [
    ("bottom", "bottom — encima del fondo de Noctalia (recomendado)"),
    ("background", "background — la misma capa que Noctalia"),
]


def _combo(titulo: str, opciones: list[tuple[str, str]], valor: str, subtitulo: str = "") -> Adw.ComboRow:
    if valor not in [v for v, _ in opciones]:
        opciones = opciones + [(valor, valor)]
    fila = Adw.ComboRow(title=titulo, subtitle=subtitulo,
                        model=Gtk.StringList.new([t for _, t in opciones]))
    fila.valores = [v for v, _ in opciones]
    fila.set_selected(fila.valores.index(valor))
    return fila


def _valor_combo(fila: Adw.ComboRow) -> str:
    return fila.valores[fila.get_selected()]


class FilaCarpeta(Adw.ActionRow):
    """Una carpeta con botones para cambiarla y, si se permite, quitarla."""

    def __init__(self, titulo: str, carpeta: str, vacia: str | None = None) -> None:
        super().__init__(title=titulo)
        self.carpeta = carpeta
        self.vacia = vacia
        cambiar = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                             tooltip_text="Elegir carpeta")
        cambiar.add_css_class("flat")
        cambiar.connect("clicked", self._elegir)
        self.add_suffix(cambiar)
        if vacia is not None:
            quitar = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text="Usar la carpeta general")
            quitar.add_css_class("flat")
            quitar.connect("clicked", lambda *_: self._poner(""))
            self.add_suffix(quitar)
        self._poner(carpeta)

    def _poner(self, carpeta: str) -> None:
        self.carpeta = carpeta
        self.set_subtitle(GLib.markup_escape_text(carpeta or self.vacia or ""))

    def _elegir(self, boton) -> None:
        dialogo = Gtk.FileDialog(title=self.get_title())
        inicial = Path(self.carpeta).expanduser() if self.carpeta else None
        if inicial and inicial.is_dir():
            dialogo.set_initial_folder(Gio.File.new_for_path(str(inicial)))

        def elegida(d, resultado):
            try:
                self._poner(_acortar(d.select_folder_finish(resultado).get_path()))
            except GLib.Error:
                pass  # cancelado
        dialogo.select_folder(boton.get_root(), None, elegida)


def _acortar(ruta: str) -> str:
    """Guarda ~/... en vez de /home/eric/..., como en la config de ejemplo."""
    casa = str(Path.home())
    return "~" + ruta[len(casa):] if ruta == casa or ruta.startswith(casa + "/") else ruta


class DialogoAjustes(Adw.PreferencesDialog):
    def __init__(self, app) -> None:
        super().__init__(title="Ajustes de lienzo", search_enabled=False)
        self.app = app
        try:
            self.crudo = config.leer_crudo()
        except Exception as e:
            app.ventana.avisar(f"No pude leer config.toml: {e}")
            self.crudo = {}
        cfg = config.Config()  # valores por defecto para lo que falte
        video = self.crudo.get("video", {})
        pausa = self.crudo.get("pause", {})
        estado = app.ventana.estado or {}
        conectados = [m["name"] for m in estado.get("monitors", [])]
        configurados = list(self.crudo.get("monitors", {}))
        monitores = conectados + [m for m in configurados if m not in conectados]

        # ---------------- General
        general = Adw.PreferencesPage(title="General", icon_name="preferences-desktop-wallpaper-symbolic")
        grupo = Adw.PreferencesGroup(title="Fondos")
        self.carpeta = FilaCarpeta("Carpeta de fondos", self.crudo.get("directory", config.DEFAULT_DIRECTORY))
        grupo.add(self.carpeta)
        paleta = self.crudo.get("palette_monitor", "")
        opciones = [("", "Automático — el primero de Hyprland")] + [(m, m) for m in monitores]
        self.paleta = _combo("Monitor de la paleta", opciones, paleta,
                             "Su fondo da los colores a Noctalia")
        grupo.add(self.paleta)
        general.add(grupo)

        grupo = Adw.PreferencesGroup(title="Vídeo")
        self.hwdec = _combo("Decodificación por hardware", DECODIFICADORES, video.get("hwdec", cfg.hwdec))
        grupo.add(self.hwdec)
        self.capa = _combo("Capa", CAPAS, video.get("layer", cfg.layer))
        grupo.add(self.capa)
        self.opciones_mpv = Adw.EntryRow(title="Opciones extra de mpv (separadas por espacios)")
        self.opciones_mpv.set_text(shlex.join(video.get("mpv_options", [])))
        grupo.add(self.opciones_mpv)
        general.add(grupo)
        general.add(self._grupo_guardar())
        self.add(general)

        # ---------------- Pausa
        pagina = Adw.PreferencesPage(title="Pausa", icon_name="media-playback-pause-symbolic")
        grupo = Adw.PreferencesGroup(title="Pausar automáticamente")
        self.completa = Adw.SwitchRow(title="Con una ventana a pantalla completa",
                                      subtitle="Solo en el monitor donde se ve; maximizar no cuenta")
        self.completa.set_active(pausa.get("fullscreen", cfg.pause_on_fullscreen))
        grupo.add(self.completa)
        self.bateria = Adw.SwitchRow(title="Con batería", subtitle="En un equipo sin batería no hace nada")
        self.bateria.set_active(pausa.get("battery", cfg.pause_on_battery))
        grupo.add(self.bateria)
        pagina.add(grupo)

        self.grupo_clases = Adw.PreferencesGroup(
            title="Ventanas de juego",
            description="Expresiones regulares contra la clase de la ventana activa "
                        "(la ves con «hyprctl clients»). Mientras una coincida, se pausa.")
        self.filas_clases: list[Adw.EntryRow] = []
        for patron in pausa.get("classes", config.DEFAULT_PAUSE_CLASSES):
            self._anadir_clase(patron)
        anadir = Adw.ButtonRow(title="Añadir clase", start_icon_name="list-add-symbolic")
        anadir.connect("activated", lambda *_: self._anadir_clase("", foco=True))
        self.grupo_clases.add(anadir)
        self._boton_anadir = anadir
        pagina.add(self.grupo_clases)
        pagina.add(self._grupo_guardar())
        self.add(pagina)

        # ---------------- Monitores
        pagina = Adw.PreferencesPage(title="Monitores", icon_name="video-display-symbolic")
        grupo = Adw.PreferencesGroup(
            title="Carpeta propia",
            description="«Siguiente» en un monitor avanza por su carpeta. Sin carpeta propia usa la general.")
        self.carpetas_monitor: dict[str, FilaCarpeta] = {}
        for m in monitores:
            actual = self.crudo.get("monitors", {}).get(m, {}).get("directory", "")
            titulo = m if m in conectados else f"{m} (desconectado)"
            fila = FilaCarpeta(titulo, actual, vacia="La carpeta general")
            self.carpetas_monitor[m] = fila
            grupo.add(fila)
        pagina.add(grupo)
        pagina.add(self._grupo_guardar())
        self.add(pagina)

    def _grupo_guardar(self) -> Adw.PreferencesGroup:
        grupo = Adw.PreferencesGroup()
        boton = Adw.ButtonRow(title="Guardar y aplicar", start_icon_name="document-save-symbolic")
        boton.add_css_class("suggested-action")
        boton.connect("activated", lambda *_: self._guardar())
        grupo.add(boton)
        return grupo

    def _anadir_clase(self, patron: str, foco: bool = False) -> None:
        fila = Adw.EntryRow(title="Clase (regex)")
        fila.set_text(patron)
        quitar = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Quitar")
        quitar.add_css_class("flat")
        quitar.connect("clicked", lambda *_: self._quitar_clase(fila))
        fila.add_suffix(quitar)
        self.filas_clases.append(fila)
        self.grupo_clases.add(fila)
        if hasattr(self, "_boton_anadir"):
            # El boton «Añadir» se queda siempre el ultimo
            self.grupo_clases.remove(self._boton_anadir)
            self.grupo_clases.add(self._boton_anadir)
        if foco:
            fila.grab_focus()

    def _quitar_clase(self, fila: Adw.EntryRow) -> None:
        self.filas_clases.remove(fila)
        self.grupo_clases.remove(fila)

    def _guardar(self) -> None:
        crudo = copy.deepcopy(self.crudo)
        crudo["directory"] = self.carpeta.carpeta
        paleta = _valor_combo(self.paleta)
        if paleta:
            crudo["palette_monitor"] = paleta
        else:
            crudo.pop("palette_monitor", None)

        try:
            opciones = shlex.split(self.opciones_mpv.get_text())
        except ValueError as e:
            self.add_toast(Adw.Toast(title=f"Opciones de mpv: {e}"))
            return
        video = crudo.setdefault("video", {})
        video.update(hwdec=_valor_combo(self.hwdec), layer=_valor_combo(self.capa), mpv_options=opciones)

        pausa = crudo.setdefault("pause", {})
        pausa.update(fullscreen=self.completa.get_active(), battery=self.bateria.get_active(),
                     classes=[t for f in self.filas_clases if (t := f.get_text().strip())])

        monitores = crudo.setdefault("monitors", {})
        for nombre, fila in self.carpetas_monitor.items():
            tabla = monitores.setdefault(nombre, {})
            if fila.carpeta:
                tabla["directory"] = fila.carpeta
            else:
                tabla.pop("directory", None)
            if not tabla:
                del monitores[nombre]
        if not monitores:
            del crudo["monitors"]

        try:
            config.escribir(crudo)
        except Exception as e:
            # El fichero viejo sigue intacto; el dialogo se queda abierto
            self.add_toast(Adw.Toast(title=GLib.markup_escape_text(f"No se ha guardado: {e}"), timeout=6))
            return
        self.close()
        self.app.reiniciar_daemon()
