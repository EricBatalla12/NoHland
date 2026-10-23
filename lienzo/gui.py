"""Interfaz grafica de lienzo (GTK4 + libadwaita).

Es otro cliente del socket de control, igual que el CLI: no reproduce nada ni
decide pausas, solo manda ordenes al daemon y ensena su estado.

Los colores salen solos de la paleta del fondo: Noctalia escribe los colores
de libadwaita en ~/.config/gtk-4.0/noctalia.css.

Todo lo que puede tardar (ordenes al daemon, miniaturas, systemctl) va en
hilos y vuelve al hilo de GTK con GLib.idle_add; si no, la ventana se
congelaria mientras mpvpaper arranca.

Cada boton es una accion de la aplicacion (app.siguiente, app.poner...), asi
que tambien se pueden lanzar por D-Bus. Asi se prueba la interfaz sin raton.
"""

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from . import config, miniaturas  # noqa: E402
from .cliente import DaemonParado, enviar  # noqa: E402
from .config import ANIMATED_EXT, IMAGE_EXT  # noqa: E402

APP_ID = "io.github.EricBatalla12.Lienzo"
# Cada cuanto se pide el estado mientras la ventana esta abierta
REFRESCO_S = 2

CSS = """
.tarjeta-fondo { padding: 6px; border-radius: 14px; }
.tarjeta-fondo:hover { background: alpha(@accent_bg_color, 0.12); }
.tarjeta-fondo.actual { box-shadow: inset 0 0 0 3px @accent_bg_color; }
.miniatura { border-radius: 10px; background: alpha(@window_fg_color, 0.08); }
.insignia { font-size: 0.75em; font-weight: bold; padding: 2px 7px; margin: 6px;
            border-radius: 6px; background: alpha(black, 0.6); color: white; }
"""


def tipo_de(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "imagen"
    if ext == ".gif":
        return "gif"
    if ext in ANIMATED_EXT:
        return "video"
    return None


def listar(carpeta: Path) -> list[Path]:
    """Mismo orden que `lienzo next`."""
    try:
        return sorted((p for p in carpeta.iterdir() if p.is_file() and tipo_de(p)),
                      key=lambda p: p.name.lower())
    except OSError:
        return []


def en_hilo(funcion, al_terminar) -> None:
    """Ejecuta funcion() en un hilo y llama al_terminar(resultado, error) en GTK."""
    def trabajo():
        try:
            resultado, error = funcion(), None
        except Exception as e:  # el error se ensena en la ventana
            resultado, error = None, e
        GLib.idle_add(lambda: al_terminar(resultado, error) and False)
    threading.Thread(target=trabajo, daemon=True).start()


# ------------------------------------------------------------ tarjeta

class Tarjeta(Gtk.FlowBoxChild):
    """Una miniatura de la cuadricula."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.set_tooltip_text(str(path))
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        caja.add_css_class("tarjeta-fondo")

        self.imagen = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self.imagen.set_size_request(224, 126)
        self.imagen.add_css_class("miniatura")
        superpuesto = Gtk.Overlay(child=self.imagen)
        tipo = tipo_de(path)
        if tipo != "imagen":
            insignia = Gtk.Label(label="VÍDEO" if tipo == "video" else "GIF",
                                 halign=Gtk.Align.END, valign=Gtk.Align.START)
            insignia.add_css_class("insignia")
            superpuesto.add_overlay(insignia)
        caja.append(superpuesto)

        nombre = Gtk.Label(label=path.name, ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=26)
        caja.append(nombre)
        self.set_child(caja)
        self._caja = caja

    def marcar(self, actual: bool) -> None:
        if actual:
            self._caja.add_css_class("actual")
        else:
            self._caja.remove_css_class("actual")

    def poner_miniatura(self, fichero: Path | None) -> None:
        if fichero:
            self.imagen.set_paintable(Gdk.Texture.new_from_filename(str(fichero)))


# ------------------------------------------------------------ ventana

class Ventana(Adw.ApplicationWindow):
    def __init__(self, app: "Aplicacion") -> None:
        super().__init__(application=app, title="lienzo", default_width=1120, default_height=760)
        self.app = app
        self.estado: dict | None = None
        self.carpeta: Path | None = None
        self.tarjetas: dict[Path, Tarjeta] = {}
        self.filas: dict[str, Adw.ActionRow] = {}
        self.monitores: list[str] = []  # en el desplegable, tras "Todos"
        self._monitor_carpeta: Gio.FileMonitor | None = None
        self._recarga_pendiente = 0
        self._consultando = False
        self._hilos_miniaturas = ThreadPoolExecutor(max_workers=4)

        # --- barra de arriba
        cabecera = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="lienzo"))
        self.desplegable = Gtk.DropDown(model=Gtk.StringList.new(["Todos los monitores"]))
        self.desplegable.set_tooltip_text("Monitor al que van Siguiente y los clics en un fondo")
        self.desplegable.connect("notify::selected", lambda *_: self._monitor_cambiado())
        cabecera.pack_start(self.desplegable)

        self.boton_pausa = Gtk.Button(action_name="app.pausa")
        self.contenido_pausa = Adw.ButtonContent(icon_name="media-playback-pause-symbolic", label="Pausar")
        self.boton_pausa.set_child(self.contenido_pausa)
        cabecera.pack_start(self.boton_pausa)
        self.boton_siguiente = siguiente = Gtk.Button(action_name="app.siguiente",
                               child=Adw.ButtonContent(icon_name="media-skip-forward-symbolic", label="Siguiente"))
        siguiente.set_tooltip_text("Siguiente fondo de la carpeta")
        cabecera.pack_start(siguiente)

        ajustes = Gtk.Button(icon_name="emblem-system-symbolic", action_name="app.ajustes", tooltip_text="Ajustes")
        cabecera.pack_end(ajustes)
        abrir = Gtk.Button(action_name="app.abrir", tooltip_text="Poner un archivo de cualquier carpeta",
                           child=Adw.ButtonContent(icon_name="document-open-symbolic", label="Abrir…"))
        cabecera.pack_end(abrir)

        # --- aviso de daemon parado
        self.aviso = Adw.Banner(title="El daemon de lienzo no está en marcha", button_label="Arrancar")
        self.aviso.set_action_name("app.arrancar-daemon")

        # --- estado por monitor
        self.lista_estado = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.lista_estado.add_css_class("boxed-list")

        # --- cuadricula de fondos
        self.cuadricula = Gtk.FlowBox(
            homogeneous=True, selection_mode=Gtk.SelectionMode.NONE,
            activate_on_single_click=True, valign=Gtk.Align.START,
            column_spacing=6, row_spacing=6, max_children_per_line=12,
        )
        self.cuadricula.connect("child-activated", self._tarjeta_pulsada)
        self.titulo_carpeta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START)
        self.titulo_carpeta.add_css_class("heading")
        self.vacio = Adw.StatusPage(icon_name="preferences-desktop-wallpaper-symbolic",
                                    title="No hay fondos en esta carpeta",
                                    description="Copia imágenes, mp4, webm o gif en ella, "
                                                "o usa «Abrir…» para elegir un archivo de otro sitio.")
        self.pila = Gtk.Stack()
        self.pila.add_named(self.cuadricula, "fondos")
        self.pila.add_named(self.vacio, "vacio")

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=12, margin_bottom=18, margin_start=18, margin_end=18)
        caja.append(self.lista_estado)
        caja.append(self.titulo_carpeta)
        caja.append(self.pila)
        desplazable = Gtk.ScrolledWindow(child=caja, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)

        vista = Adw.ToolbarView()
        vista.add_top_bar(cabecera)
        vista.add_top_bar(self.aviso)
        vista.set_content(desplazable)
        self.toasts = Adw.ToastOverlay(child=vista)
        self.set_content(self.toasts)

        self._refrescar()
        self._temporizador = GLib.timeout_add_seconds(REFRESCO_S, self._refrescar)
        self.connect("close-request", self._al_cerrar)

    def _al_cerrar(self, *_):
        GLib.source_remove(self._temporizador)
        self._hilos_miniaturas.shutdown(wait=False, cancel_futures=True)
        return False

    def avisar(self, texto: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(texto), timeout=3))

    # --- monitor elegido

    def monitor_elegido(self) -> str | None:
        i = self.desplegable.get_selected()
        return self.monitores[i - 1] if 0 < i <= len(self.monitores) else None

    def elegir_monitor(self, nombre: str | None) -> None:
        self.desplegable.set_selected(self.monitores.index(nombre) + 1 if nombre in self.monitores else 0)

    def _monitor_cambiado(self) -> None:
        self._actualizar_carpeta()
        self._marcar_actuales()

    # --- estado del daemon

    def _refrescar(self) -> bool:
        if not self._consultando:
            self._consultando = True
            en_hilo(lambda: enviar({"cmd": "status"}, timeout=10), self._estado_recibido)
        return True  # sigue el temporizador

    def _estado_recibido(self, respuesta, error) -> None:
        self._consultando = False
        if isinstance(error, DaemonParado):
            # Fuera el estado viejo: sin daemon no hay nada reproduciendose
            self.estado = None
            self.aviso.set_revealed(True)
            for boton in (self.boton_pausa, self.boton_siguiente):
                boton.set_sensitive(False)
            self.lista_estado.remove_all()
            self.lista_estado.set_visible(False)
            self.filas = {}
            self._actualizar_carpeta()
            self._marcar_actuales()
            return
        if error:
            self.avisar(f"Error al consultar el daemon: {error}")
            return
        self.aplicar_respuesta(respuesta)

    def aplicar_respuesta(self, respuesta: dict) -> None:
        """Pinta el estado. Las ordenes tambien lo devuelven, asi que tras
        pulsar un boton se ve el cambio sin esperar al siguiente refresco."""
        self.aviso.set_revealed(False)
        for boton in (self.boton_pausa, self.boton_siguiente):
            boton.set_sensitive(True)
        self.lista_estado.set_visible(True)
        self.estado = respuesta["status"]
        self._pintar_estado()

    def _pintar_estado(self) -> None:
        e = self.estado
        nombres = [m["name"] for m in e["monitors"]]
        if nombres != self.monitores:
            elegido = self.monitor_elegido()
            self.monitores = nombres
            self.desplegable.set_model(Gtk.StringList.new(["Todos los monitores"] + nombres))
            self.elegir_monitor(elegido)

        manual = e["manual_pause"]
        self.contenido_pausa.set_icon_name("media-playback-start-symbolic" if manual else "media-playback-pause-symbolic")
        self.contenido_pausa.set_label("Reanudar" if manual else "Pausar")
        self.boton_pausa.set_tooltip_text("Quitar la pausa manual" if manual else
                                          "Pausar los vídeos hasta que pulses Reanudar")

        for nombre in list(self.filas):
            if nombre not in nombres:
                self.lista_estado.remove(self.filas.pop(nombre))
        for m in e["monitors"]:
            fila = self.filas.get(m["name"])
            if fila is None:
                fila = Adw.ActionRow(title=m["name"])
                # Gtk.Picture pide el tamano natural de la imagen y estiraria la
                # fila; Gtk.Image con una textura ya reducida se queda en 96x54
                fila.miniatura = Gtk.Image(pixel_size=96, margin_top=6, margin_bottom=6)
                fila.miniatura.ruta = None
                fila.add_prefix(fila.miniatura)
                fila.icono_paleta = Gtk.Image(icon_name="color-select-symbolic",
                                              tooltip_text="De este monitor salen los colores de Noctalia")
                fila.add_suffix(fila.icono_paleta)
                self.filas[m["name"]] = fila
                self.lista_estado.append(fila)
            fila.set_subtitle(GLib.markup_escape_text(_describir(m)))
            fila.icono_paleta.set_visible(m["palette"])
            actual = Path(m["current"]) if m["current"] else None
            if actual != fila.miniatura.ruta:
                fila.miniatura.ruta = actual
                fila.miniatura.set_from_paintable(None)
                if actual:
                    self._pedir_miniatura(actual, fila.miniatura.set_from_paintable, textura=True)

        self._actualizar_carpeta()
        self._marcar_actuales()

    # --- cuadricula

    def _actualizar_carpeta(self) -> None:
        if self.estado is None:
            carpeta = config.load()[0].directory
        else:
            elegido = self.monitor_elegido()
            datos = next((m for m in self.estado["monitors"] if m["name"] == elegido), None)
            carpeta = Path(datos["directory"] if datos else self.estado["directory"])
        if carpeta != self.carpeta:
            self.carpeta = carpeta
            self._vigilar_carpeta()
            self._recargar_fondos()

    def _vigilar_carpeta(self) -> None:
        # Gio avisa cuando cambia la carpeta: sin releerla cada pocos segundos
        if self._monitor_carpeta:
            self._monitor_carpeta.cancel()
        try:
            self._monitor_carpeta = Gio.File.new_for_path(str(self.carpeta)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._monitor_carpeta.connect("changed", lambda *_: self._programar_recarga())
        except GLib.Error:
            self._monitor_carpeta = None

    def _programar_recarga(self) -> None:
        # Copiar varios ficheros dispara muchos avisos: se recarga una vez
        if self._recarga_pendiente:
            GLib.source_remove(self._recarga_pendiente)
        self._recarga_pendiente = GLib.timeout_add(400, self._recargar_fondos)

    def _recargar_fondos(self) -> bool:
        self._recarga_pendiente = 0
        fondos = listar(self.carpeta)
        self.titulo_carpeta.set_label(f"{self.carpeta}  ·  {len(fondos)} fondos")
        if [t for t in self.tarjetas] != fondos:
            self.cuadricula.remove_all()
            self.tarjetas = {}
            for path in fondos:
                tarjeta = Tarjeta(path)
                self.tarjetas[path] = tarjeta
                self.cuadricula.append(tarjeta)
                self._pedir_miniatura(path, tarjeta.poner_miniatura)
        self.pila.set_visible_child_name("fondos" if fondos else "vacio")
        self._marcar_actuales()
        return False

    def _pedir_miniatura(self, path: Path, al_terminar, textura: bool = False) -> None:
        def hecho(futuro):
            fichero = None if futuro.cancelled() or futuro.exception() else futuro.result()
            if textura:
                valor = _textura_pequena(fichero) if fichero else None
            else:
                valor = fichero
            GLib.idle_add(lambda: al_terminar(valor) and False)
        try:
            futuro = self._hilos_miniaturas.submit(miniaturas.miniatura, path, tipo_de(path) != "imagen")
        except RuntimeError:  # la ventana se esta cerrando
            return
        futuro.add_done_callback(hecho)

    def _marcar_actuales(self) -> None:
        if self.estado is None:
            actuales = set()
        else:
            elegido = self.monitor_elegido()
            actuales = {Path(m["current"]) for m in self.estado["monitors"]
                        if m["current"] and (elegido is None or m["name"] == elegido)}
        for path, tarjeta in self.tarjetas.items():
            tarjeta.marcar(path in actuales)

    def _tarjeta_pulsada(self, _cuadricula, tarjeta: Tarjeta) -> None:
        self.app.activate_action("poner", GLib.Variant("s", str(tarjeta.path)))


def _textura_pequena(fichero: Path) -> Gdk.Texture:
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(fichero), 96, 54, True)
    return Gdk.MemoryTexture.new(
        pixbuf.get_width(), pixbuf.get_height(),
        Gdk.MemoryFormat.R8G8B8A8 if pixbuf.get_has_alpha() else Gdk.MemoryFormat.R8G8B8,
        pixbuf.read_pixel_bytes(), pixbuf.get_rowstride())


def _describir(m: dict) -> str:
    if not m["current"]:
        return "sin fondo"
    partes = [Path(m["current"]).name, {"imagen": "imagen", "video": "vídeo", "gif": "GIF"}.get(m["kind"], "?")]
    if m.get("backend") == "mpvpaper":
        hw = {None: "arrancando", "no": "por CPU"}.get(m.get("hwdec"), m.get("hwdec"))
        partes.append(f"decodificación {hw}")
        partes.append("en pausa: " + "; ".join(m["reasons"]) if m["paused"] else "reproduciendo")
    elif m.get("error"):
        partes.append(m["error"])
    return "  ·  ".join(partes)


# ------------------------------------------------------------ aplicacion

class Aplicacion(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self.ventana: Ventana | None = None
        acciones = {
            "siguiente": (None, self._siguiente),
            "pausa": (None, self._pausa),
            "poner": ("s", self._poner),
            "monitor": ("s", self._elegir_monitor),
            "abrir": (None, self._abrir),
            "ajustes": (None, self._ajustes),
            "arrancar-daemon": (None, self._arrancar_daemon),
            "salir": (None, lambda *_: self.quit()),
        }
        for nombre, (tipo, funcion) in acciones.items():
            accion = Gio.SimpleAction(name=nombre, parameter_type=GLib.VariantType(tipo) if tipo else None)
            accion.connect("activate", funcion)
            self.add_action(accion)
        self.set_accels_for_action("app.salir", ["<Control>q", "<Control>w"])
        self.set_accels_for_action("app.siguiente", ["<Control>Right"])
        self.set_accels_for_action("app.pausa", ["space"])

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        if self.ventana is None:
            self.ventana = Ventana(self)
        self.ventana.present()

    # --- ordenes al daemon

    def orden(self, peticion: dict, hecho: str) -> None:
        def al_terminar(respuesta, error):
            v = self.ventana
            if isinstance(error, DaemonParado):
                v.aviso.set_revealed(True)
                v.avisar(str(error))
            elif error:
                v.avisar(f"Error: {error}")
            elif not respuesta.get("ok"):
                v.avisar(respuesta.get("error", "error desconocido"))
            else:
                v.aplicar_respuesta(respuesta)
                if hecho:
                    v.avisar(hecho)
        en_hilo(lambda: enviar(peticion), al_terminar)

    def _con_monitor(self, peticion: dict) -> dict:
        monitor = self.ventana.monitor_elegido()
        if monitor:
            peticion["monitor"] = monitor
        return peticion

    def _donde(self) -> str:
        return self.ventana.monitor_elegido() or "todos los monitores"

    def _siguiente(self, *_):
        self.orden(self._con_monitor({"cmd": "next"}), "")

    def _pausa(self, *_):
        manual = bool(self.ventana.estado and self.ventana.estado["manual_pause"])
        self.orden({"cmd": "resume" if manual else "pause"}, "Pausa quitada" if manual else "Vídeos en pausa")

    def _poner(self, _accion, parametro):
        path = Path(parametro.get_string())
        self.ventana.avisar(f"Poniendo {path.name}…")
        self.orden(self._con_monitor({"cmd": "set", "path": str(path)}), f"{path.name} en {self._donde()}")

    def _elegir_monitor(self, _accion, parametro):
        self.ventana.elegir_monitor(parametro.get_string() or None)

    def _abrir(self, *_):
        filtro = Gtk.FileFilter(name="Fondos (imágenes, vídeos y GIF)")
        for ext in sorted(IMAGE_EXT | ANIMATED_EXT):
            filtro.add_suffix(ext.lstrip("."))
        filtros = Gio.ListStore.new(Gtk.FileFilter)
        filtros.append(filtro)
        dialogo = Gtk.FileDialog(title="Elegir fondo", filters=filtros, default_filter=filtro)
        if self.ventana.carpeta:
            dialogo.set_initial_folder(Gio.File.new_for_path(str(self.ventana.carpeta)))

        def elegido(d, resultado):
            try:
                fichero = d.open_finish(resultado)
            except GLib.Error:
                return  # cancelado
            self.activate_action("poner", GLib.Variant("s", fichero.get_path()))
        dialogo.open(self.ventana, None, elegido)

    def _ajustes(self, *_):
        from .ajustes import DialogoAjustes
        DialogoAjustes(self).present(self.ventana)

    def _arrancar_daemon(self, *_):
        self.reiniciar_daemon(arrancar=True)

    def reiniciar_daemon(self, arrancar: bool = False) -> None:
        verbo = "start" if arrancar else "restart"

        def al_terminar(r, error):
            if error or r.returncode:
                detalle = error or r.stderr.strip()
                self.ventana.avisar(f"systemctl --user {verbo} lienzo falló: {detalle}")
            else:
                self.ventana.avisar("lienzo arrancado" if arrancar else "Ajustes aplicados; lienzo reiniciado")
            self.ventana._refrescar()
        en_hilo(lambda: subprocess.run(["systemctl", "--user", verbo, "lienzo"],
                                       capture_output=True, text=True, timeout=30), al_terminar)


def main() -> int:
    return Aplicacion().run([])
