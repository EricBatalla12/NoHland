"""Interfaz grafica de NoHland (GTK4 + libadwaita).

Es otro cliente del socket de control, igual que el CLI: no reproduce nada ni
decide pausas, solo manda ordenes al daemon y ensena su estado.

Los colores salen solos de la paleta del fondo: Noctalia escribe los colores
de libadwaita en ~/.config/gtk-4.0/noctalia.css, y el CSS de aqui solo usa
esos colores con nombre (@accent_bg_color...) o blanco sobre la imagen.

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
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import __version__, config, miniaturas  # noqa: E402
from .cliente import DaemonParado, enviar  # noqa: E402
from .config import ANIMATED_EXT, IMAGE_EXT  # noqa: E402

APP_ID = "io.github.EricBatalla12.NoHland"
# Cada cuanto se pide el estado mientras la ventana esta abierta
REFRESCO_S = 2
ALTO_PORTADA = 340

CSS = """
/* --- portada: el fondo actual en grande */
.portada { border-radius: 24px; background: alpha(@window_fg_color, 0.06); }
.portada-velo {
  background-image: linear-gradient(to top, alpha(black, 0.82) 0%, alpha(black, 0.35) 45%, transparent 75%),
                    linear-gradient(to right, alpha(black, 0.45) 0%, transparent 60%);
}
.portada-vacia { background-image: linear-gradient(135deg, alpha(@accent_bg_color, 0.55), alpha(@accent_bg_color, 0.08)); }
.portada-etiqueta { color: alpha(white, 0.75); font-size: 0.8em; font-weight: 800; letter-spacing: 2px; }
.portada-titulo { color: white; font-size: 2.4em; font-weight: 800; }
.chip { padding: 3px 11px; border-radius: 999px; background: alpha(white, 0.16); color: white;
        font-size: 0.85em; font-weight: 700; }
.chip.acento { background: @accent_bg_color; color: @accent_fg_color; }
.chip.pausa { background: alpha(#f6c343, 0.9); color: #2b2100; }
.boton-redondo { min-width: 54px; min-height: 54px; padding: 0; border-radius: 999px;
                 background: alpha(white, 0.16); color: white; }
.boton-redondo:hover { background: alpha(white, 0.26); }
.boton-redondo.principal { min-width: 72px; min-height: 72px; background: @accent_bg_color; color: @accent_fg_color; }
.boton-redondo.principal:hover { background: mix(@accent_bg_color, white, 0.15); }
.boton-redondo:disabled { opacity: 0.4; }

/* --- coleccion */
.tarjeta { border-radius: 18px; background: alpha(@window_fg_color, 0.06);
           transition: box-shadow 200ms ease, outline-color 200ms ease;
           outline: 3px solid transparent; outline-offset: 2px; }
flowboxchild:hover .tarjeta { box-shadow: 0 10px 28px alpha(black, 0.45); }
flowboxchild:focus-visible .tarjeta { outline-color: alpha(@accent_bg_color, 0.6); }
.tarjeta.actual { outline-color: @accent_bg_color; }
.tarjeta-velo { background-image: linear-gradient(to top, alpha(black, 0.78), transparent); padding: 28px 14px 12px 14px; }
.tarjeta-nombre { color: white; font-weight: 800; }
.tarjeta-tipo { color: alpha(white, 0.7); font-size: 0.8em; font-weight: 600; }
.insignia { margin: 10px; padding: 3px 9px; border-radius: 999px; font-size: 0.75em; font-weight: 800;
            background: alpha(black, 0.55); color: white; }
.insignia.en-uso { background: @accent_bg_color; color: @accent_fg_color; }
flowboxchild { padding: 0; border-radius: 18px; background: none; }
.monitor-fila image { border-radius: 8px; }
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


NOMBRE_TIPO = {"imagen": "Imagen", "video": "Vídeo", "gif": "GIF"}
# Webs para conseguir fondos, en el boton «Conseguir fondos». Solo enlaces:
# NoHland no descarga nada por su cuenta.
SITIOS = [
    ("Wallhaven", "Imágenes en alta resolución de anime y juegos, con filtro de contenido apto",
     "https://wallhaven.cc/search?categories=010&purity=100&sorting=toplist&atleast=1920x1080&ratios=16x9",
     "image-x-generic-symbolic"),
    ("Wallpaper Abyss", "Imágenes · catálogo enorme por serie y por juego",
     "https://wall.alphacoders.com/", "image-x-generic-symbolic"),
    ("MoeWalls", "Fondos animados de anime y juegos, en mp4",
     "https://moewalls.com/", "camera-video-symbolic"),
    ("MotionBGs", "Fondos animados en mp4, por categorías",
     "https://motionbgs.com/", "camera-video-symbolic"),
    ("Wallpaper Engine", "Workshop de Steam · en NoHland solo sirven los de tipo Vídeo",
     "https://steamcommunity.com/app/431960/workshop/", "applications-games-symbolic"),
]

ICONO_TIPO = {"imagen": "image-x-generic-symbolic", "video": "camera-video-symbolic", "gif": "camera-video-symbolic"}


def listar(carpeta: Path) -> list[Path]:
    """Mismo orden que `nohland next`."""
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


def _textura(fichero: Path | None, ancho: int | None = None, alto: int | None = None) -> Gdk.Texture | None:
    if not fichero:
        return None
    if ancho is None:
        return Gdk.Texture.new_from_filename(str(fichero))
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(fichero), ancho, alto, True)
    return Gdk.MemoryTexture.new(
        pixbuf.get_width(), pixbuf.get_height(),
        Gdk.MemoryFormat.R8G8B8A8 if pixbuf.get_has_alpha() else Gdk.MemoryFormat.R8G8B8,
        pixbuf.read_pixel_bytes(), pixbuf.get_rowstride())


def _etiqueta(texto: str = "", *clases: str, **kw) -> Gtk.Label:
    l = Gtk.Label(label=texto, **kw)
    for c in clases:
        l.add_css_class(c)
    return l


# ------------------------------------------------------------ imagen recortada

class Cubierta(Gtk.Widget):
    """Pinta una textura recortada para llenar su caja (como object-fit: cover).

    Gtk.Picture pide como tamano natural el de la imagen y estira lo que la
    contiene; este widget decide su propio alto: fijo (portada) o 16:9 segun
    el ancho que le den (tarjetas).
    """

    def __init__(self, alto: int | None = None, ancho_minimo: int = 0) -> None:
        super().__init__()
        self.textura: Gdk.Texture | None = None
        self.alto = alto
        self.ancho_minimo = ancho_minimo
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def poner(self, textura: Gdk.Texture | None) -> None:
        self.textura = textura
        self.queue_draw()

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientacion, para):
        if orientacion == Gtk.Orientation.HORIZONTAL:
            return self.ancho_minimo, max(self.ancho_minimo, 320), -1, -1
        if self.alto:
            return self.alto, self.alto, -1, -1
        alto = round((para if para > 0 else 320) * 9 / 16)
        return alto, alto, -1, -1

    def do_snapshot(self, snapshot) -> None:
        if not self.textura:
            return
        w, h = self.get_width(), self.get_height()
        tw, th = self.textura.get_width(), self.textura.get_height()
        escala = max(w / tw, h / th)
        dw, dh = tw * escala, th * escala
        snapshot.push_clip(Graphene.Rect().init(0, 0, w, h))
        snapshot.append_scaled_texture(self.textura, Gsk.ScalingFilter.TRILINEAR,
                                       Graphene.Rect().init((w - dw) / 2, (h - dh) / 2, dw, dh))
        snapshot.pop()


# ------------------------------------------------------------ tarjeta

class Tarjeta(Gtk.FlowBoxChild):
    """Un fondo de la coleccion: imagen, nombre encima y etiquetas."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.tipo = tipo_de(path)
        self.set_tooltip_text(str(path))

        self.cubierta = Cubierta(ancho_minimo=240)
        caja = Gtk.Overlay(child=self.cubierta, overflow=Gtk.Overflow.HIDDEN)
        caja.add_css_class("tarjeta")

        velo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END, spacing=2)
        velo.add_css_class("tarjeta-velo")
        velo.append(_etiqueta(path.stem, "tarjeta-nombre", xalign=0, ellipsize=Pango.EllipsizeMode.END))
        velo.append(_etiqueta(f"{NOMBRE_TIPO[self.tipo]} · {path.suffix.lstrip('.').upper()}",
                              "tarjeta-tipo", xalign=0))
        caja.add_overlay(velo)

        if self.tipo != "imagen":
            insignia = Gtk.Box(spacing=4, halign=Gtk.Align.END, valign=Gtk.Align.START)
            insignia.add_css_class("insignia")
            insignia.append(Gtk.Image(icon_name="media-playback-start-symbolic", pixel_size=10))
            insignia.append(Gtk.Label(label="GIF" if self.tipo == "gif" else "ANIMADO"))
            caja.add_overlay(insignia)

        self.en_uso = Gtk.Box(spacing=4, halign=Gtk.Align.START, valign=Gtk.Align.START, visible=False)
        self.en_uso.add_css_class("insignia")
        self.en_uso.add_css_class("en-uso")
        self.en_uso.append(Gtk.Image(icon_name="object-select-symbolic", pixel_size=12))
        self.en_uso.append(Gtk.Label(label="EN USO"))
        caja.add_overlay(self.en_uso)

        self._caja = caja
        self.set_child(caja)

    def marcar(self, actual: bool) -> None:
        self.en_uso.set_visible(actual)
        if actual:
            self._caja.add_css_class("actual")
        else:
            self._caja.remove_css_class("actual")


# ------------------------------------------------------------ webs de fondos

class PopoverWebs(Gtk.Popover):
    """Lista de webs de fondos y recordatorio de donde guardar lo descargado."""

    def __init__(self, ventana: "Ventana") -> None:
        super().__init__()
        self.ventana = ventana
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=12, margin_bottom=12, margin_start=12, margin_end=12, width_request=440)
        caja.append(_etiqueta("Conseguir fondos", "heading", xalign=0))

        lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lista.add_css_class("boxed-list")
        for nombre, texto, url, icono in SITIOS:
            fila = Adw.ActionRow(title=nombre, subtitle=GLib.markup_escape_text(texto),
                                 activatable=True, tooltip_text=url)
            fila.add_prefix(Gtk.Image(icon_name=icono))
            fila.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
            fila.connect("activated", lambda _f, u=url: self._abrir(u))
            lista.append(fila)
        caja.append(lista)

        # Lo que se guarde en la carpeta aparece solo: la ventana la vigila
        self.nota = _etiqueta("", "dim-label", "caption", xalign=0, wrap=True, max_width_chars=50)
        caja.append(self.nota)
        carpeta = Gtk.Button(halign=Gtk.Align.START,
                             child=Adw.ButtonContent(icon_name="folder-open-symbolic", label="Abrir la carpeta"))
        carpeta.connect("clicked", lambda *_: (self.popdown(), ventana._abrir_carpeta()))
        caja.append(carpeta)
        self.set_child(caja)
        self.connect("show", lambda *_: self._actualizar_nota())

    def _actualizar_nota(self) -> None:
        carpeta = str(self.ventana.carpeta or "").replace(str(Path.home()), "~", 1)
        self.nota.set_label(f"Guarda lo que descargues en {carpeta}: aparece solo en la colección. "
                            "De Wallpaper Engine, enlaza el .mp4 del fondo en esa carpeta.")

    def _abrir(self, url: str) -> None:
        self.popdown()
        self.ventana.app.activate_action("web", GLib.Variant("s", url))


# ------------------------------------------------------------ portada

class Portada(Gtk.Overlay):
    """El fondo que suena ahora, en grande, con sus controles."""

    def __init__(self) -> None:
        self.cubierta = Cubierta(alto=ALTO_PORTADA)
        super().__init__(child=self.cubierta, overflow=Gtk.Overflow.HIDDEN)
        self.add_css_class("portada")
        self.ruta: Path | None = None

        self.velo = Gtk.Box()
        self.velo.add_css_class("portada-velo")
        self.add_overlay(self.velo)

        abajo = Gtk.Box(spacing=24, valign=Gtk.Align.END, margin_start=32, margin_end=28, margin_bottom=26)
        textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True, valign=Gtk.Align.END)
        self.etiqueta = _etiqueta("", "portada-etiqueta", xalign=0)
        self.titulo = _etiqueta("", "portada-titulo", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.chips = Gtk.Box(spacing=6, margin_top=6)
        textos.append(self.etiqueta)
        textos.append(self.titulo)
        textos.append(self.chips)
        abajo.append(textos)

        botones = Gtk.Box(spacing=14, valign=Gtk.Align.END)
        self.siguiente = Gtk.Button(action_name="app.siguiente", valign=Gtk.Align.CENTER,
                                    tooltip_text="Siguiente fondo de la carpeta (Ctrl+→)",
                                    child=Gtk.Image(icon_name="media-skip-forward-symbolic", pixel_size=22))
        self.siguiente.add_css_class("boton-redondo")
        self.icono_pausa = Gtk.Image(icon_name="media-playback-pause-symbolic", pixel_size=30)
        self.pausa = Gtk.Button(action_name="app.pausa", child=self.icono_pausa, valign=Gtk.Align.CENTER)
        self.pausa.add_css_class("boton-redondo")
        self.pausa.add_css_class("principal")
        botones.append(self.siguiente)
        botones.append(self.pausa)
        abajo.append(botones)
        self.add_overlay(abajo)

    def mostrar(self, monitor: dict | None, varios: bool, manual: bool, sin_daemon: bool,
                hay_video: bool = False) -> None:
        if sin_daemon:
            self._vacia("NoHland está parado", "Pulsa «Arrancar» arriba para volver a poner los fondos")
        elif not monitor or not monitor["current"]:
            nombre = monitor["name"] if monitor else ""
            self._vacia("Sin fondo", f"Elige uno de la colección{' para ' + nombre if nombre else ''}")
        else:
            ruta = Path(monitor["current"])
            self.velo.remove_css_class("portada-vacia")
            self.etiqueta.set_label(("AHORA EN " + monitor["name"]).upper() if varios else "AHORA EN TU ESCRITORIO")
            self.titulo.set_label(ruta.stem)
            chips = [(NOMBRE_TIPO.get(monitor["kind"], "?"), "")]
            if monitor.get("backend") == "mpvpaper":
                hw = {None: "arrancando", "no": "por CPU"}.get(monitor.get("hwdec"), monitor.get("hwdec"))
                chips.append((f"Decodifica {hw}", ""))
                if monitor["paused"]:
                    chips += [(f"En pausa · {r}", "pausa") for r in monitor["reasons"]]
                else:
                    chips.append(("Reproduciendo", "acento"))
            if monitor.get("palette") and varios:
                chips.append(("Paleta de Noctalia", ""))
            if monitor.get("error"):
                chips.append((monitor["error"], "pausa"))
            self._poner_chips(chips)
            if ruta != self.ruta:
                self.ruta = ruta
                self.cubierta.poner(None)
        self.icono_pausa.set_from_icon_name("media-playback-start-symbolic" if manual else "media-playback-pause-symbolic")
        self.pausa.set_tooltip_text("Quitar la pausa manual (Ctrl+Espacio)" if manual else "Pausar los vídeos (Ctrl+Espacio)")
        # Con solo imagenes fijas no hay nada que pausar; con la pausa manual
        # puesta se deja activo para poder quitarla
        self.pausa.set_sensitive(not sin_daemon and (hay_video or manual))
        self.siguiente.set_sensitive(not sin_daemon)

    def _vacia(self, titulo: str, texto: str) -> None:
        self.ruta = None
        self.cubierta.poner(None)
        self.velo.add_css_class("portada-vacia")
        self.etiqueta.set_label("NOHLAND")
        self.titulo.set_label(titulo)
        self._poner_chips([(texto, "")])

    def _poner_chips(self, chips: list[tuple[str, str]]) -> None:
        while hijo := self.chips.get_first_child():
            self.chips.remove(hijo)
        for texto, clase in chips:
            self.chips.append(_etiqueta(texto, "chip", *([clase] if clase else []),
                                        ellipsize=Pango.EllipsizeMode.END, max_width_chars=48))


# ------------------------------------------------------------ ventana

class Ventana(Adw.ApplicationWindow):
    def __init__(self, app: "Aplicacion") -> None:
        super().__init__(application=app, title="NoHland", default_width=1320, default_height=860)
        self.app = app
        self.estado: dict | None = None
        self.carpeta: Path | None = None
        self.tarjetas: dict[Path, Tarjeta] = {}
        self.filas: dict[str, Adw.ActionRow] = {}
        self.monitores: list[str] = []
        self._monitor_carpeta: Gio.FileMonitor | None = None
        self._recarga_pendiente = 0
        self._consultando = False
        self._portada_pedida: Path | None = None
        self._hilos_miniaturas = ThreadPoolExecutor(max_workers=4)

        # --- barra de arriba
        cabecera = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="NoHland"))
        self.selector_monitor = Adw.ToggleGroup(visible=False, tooltip_text="Monitor al que van los cambios")
        self.selector_monitor.connect("notify::active-name", lambda *_: self._monitor_cambiado())
        cabecera.pack_start(self.selector_monitor)

        menu = Gio.Menu()
        menu.append("Ajustes", "app.ajustes")
        menu.append("Acerca de NoHland", "app.acerca")
        cabecera.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menú"))
        abrir = Gtk.Button(action_name="app.abrir", tooltip_text="Poner un archivo de cualquier carpeta",
                           child=Adw.ButtonContent(icon_name="document-open-symbolic", label="Abrir…"))
        cabecera.pack_end(abrir)
        self.boton_webs = Gtk.MenuButton(popover=PopoverWebs(self),
                                         tooltip_text="Webs donde descargar fondos de anime y juegos",
                                         child=Adw.ButtonContent(icon_name="folder-download-symbolic",
                                                                 label="Conseguir fondos"))
        cabecera.pack_end(self.boton_webs)

        self.aviso = Adw.Banner(title="El daemon de NoHland no está en marcha", button_label="Arrancar")
        self.aviso.set_action_name("app.arrancar-daemon")

        # --- portada
        self.portada = Portada()

        # --- monitores (solo con mas de uno)
        self.lista_monitores = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, visible=False)
        self.lista_monitores.add_css_class("boxed-list")

        # --- cabecera de la coleccion
        titulo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        titulo.append(_etiqueta("Colección", "title-2", xalign=0))
        self.ruta_carpeta = _etiqueta("", "dim-label", "caption", xalign=0, ellipsize=Pango.EllipsizeMode.START)
        titulo.append(self.ruta_carpeta)

        self.filtro = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
        for nombre, texto in (("todo", "Todo"), ("imagen", "Imágenes"), ("animado", "Animados")):
            self.filtro.add(Adw.Toggle(name=nombre, label=texto))
        self.filtro.set_active_name("todo")
        self.filtro.connect("notify::active-name", lambda *_: self._filtrar())

        self.busqueda = Gtk.SearchEntry(placeholder_text="Buscar fondos", valign=Gtk.Align.CENTER, width_chars=22)
        self.busqueda.connect("search-changed", lambda *_: self._filtrar())

        carpeta = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                             tooltip_text="Abrir la carpeta en el gestor de archivos")
        carpeta.add_css_class("flat")
        carpeta.connect("clicked", lambda *_: self._abrir_carpeta())

        barra = Gtk.Box(spacing=12)
        for w in (titulo, self.filtro, self.busqueda, carpeta):
            barra.append(w)

        # --- cuadricula
        self.cuadricula = Gtk.FlowBox(
            homogeneous=True, selection_mode=Gtk.SelectionMode.NONE,
            activate_on_single_click=True, valign=Gtk.Align.START,
            column_spacing=20, row_spacing=20, min_children_per_line=2, max_children_per_line=6,
        )
        self.cuadricula.set_filter_func(self._pasa_filtro)
        self.cuadricula.connect("child-activated", self._tarjeta_pulsada)
        self.vacio = Adw.StatusPage(icon_name="preferences-desktop-wallpaper-symbolic",
                                    title="No hay fondos en esta carpeta",
                                    description="Copia imágenes, mp4, webm o gif en ella, "
                                                "o usa «Abrir…» para elegir un archivo de otro sitio.")
        conseguir = Gtk.Button(label="Conseguir fondos", action_name="app.conseguir", halign=Gtk.Align.CENTER)
        conseguir.add_css_class("pill")
        conseguir.add_css_class("suggested-action")
        self.vacio.set_child(conseguir)
        self.sin_resultados = Adw.StatusPage(icon_name="edit-find-symbolic", title="Nada coincide",
                                             description="Prueba con otro filtro o búsqueda.")
        self.pila = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pila.add_named(self.cuadricula, "fondos")
        self.pila.add_named(self.vacio, "vacio")
        self.pila.add_named(self.sin_resultados, "nada")

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22,
                       margin_top=8, margin_bottom=32, margin_start=28, margin_end=28)
        for w in (self.portada, self.lista_monitores, barra, self.pila):
            caja.append(w)
        desplazable = Gtk.ScrolledWindow(child=Adw.Clamp(child=caja, maximum_size=1600, tightening_threshold=1200),
                                         vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)

        vista = Adw.ToolbarView()
        vista.add_top_bar(cabecera)
        vista.add_top_bar(self.aviso)
        vista.set_content(desplazable)
        self.toasts = Adw.ToastOverlay(child=vista)
        self.set_content(self.toasts)

        atajo = Gtk.ShortcutController()
        atajo.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Control>f"),
                                        action=Gtk.CallbackAction.new(lambda *_: self.busqueda.grab_focus() or True)))
        self.add_controller(atajo)

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
        nombre = self.selector_monitor.get_active_name()
        return nombre if nombre in self.monitores else None

    def elegir_monitor(self, nombre: str | None) -> None:
        if len(self.monitores) > 1:
            self.selector_monitor.set_active_name(nombre if nombre in self.monitores else "*")

    def _monitor_cambiado(self) -> None:
        if self.estado:
            self._pintar_estado()

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
            self.portada.mostrar(None, False, False, sin_daemon=True)
            self.lista_monitores.set_visible(False)
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
        self.estado = respuesta["status"]
        self._pintar_estado()

    def _pintar_estado(self) -> None:
        e = self.estado
        nombres = [m["name"] for m in e["monitors"]]
        if nombres != self.monitores:
            elegido = self.monitor_elegido()
            self.monitores = nombres
            self.selector_monitor.remove_all()
            self.selector_monitor.add(Adw.Toggle(name="*", label="Todos"))
            for n in nombres:
                self.selector_monitor.add(Adw.Toggle(name=n, label=n))
            self.selector_monitor.set_visible(len(nombres) > 1)
            self.elegir_monitor(elegido)

        # En la portada: el monitor elegido o, con «Todos», el de la paleta
        elegido = self.monitor_elegido()
        portada = next((m for m in e["monitors"] if m["name"] == elegido), None) or \
            next((m for m in e["monitors"] if m["palette"]), None)
        hay_video = any(m.get("backend") == "mpvpaper" for m in e["monitors"])
        self.portada.mostrar(portada, len(nombres) > 1, e["manual_pause"], sin_daemon=False, hay_video=hay_video)
        # Se pide una vez por fondo; si ffmpeg falla no se reintenta cada 2 s
        if portada and portada["current"] and self.portada.ruta != self._portada_pedida:
            self._portada_pedida = self.portada.ruta
            self._pedir_miniatura(self.portada.ruta, self._portada_lista, miniaturas.PORTADA)

        self._pintar_monitores(e["monitors"] if len(nombres) > 1 else [])
        self._actualizar_carpeta()
        self._marcar_actuales()

    def _portada_lista(self, fichero: Path | None, origen: Path) -> None:
        if origen == self.portada.ruta:
            self.portada.cubierta.poner(_textura(fichero))

    def _pintar_monitores(self, monitores: list[dict]) -> None:
        self.lista_monitores.set_visible(bool(monitores))
        nombres = [m["name"] for m in monitores]
        for nombre in list(self.filas):
            if nombre not in nombres:
                self.lista_monitores.remove(self.filas.pop(nombre))
        for m in monitores:
            fila = self.filas.get(m["name"])
            if fila is None:
                fila = Adw.ActionRow(title=m["name"], activatable=True)
                fila.add_css_class("monitor-fila")
                fila.connect("activated", lambda f: self.elegir_monitor(f.get_title()))
                fila.miniatura = Gtk.Image(pixel_size=96, margin_top=6, margin_bottom=6)
                fila.miniatura.ruta = None
                fila.add_prefix(fila.miniatura)
                fila.icono_paleta = Gtk.Image(icon_name="color-select-symbolic",
                                              tooltip_text="De este monitor salen los colores de Noctalia")
                fila.add_suffix(fila.icono_paleta)
                self.filas[m["name"]] = fila
                self.lista_monitores.append(fila)
            fila.set_subtitle(GLib.markup_escape_text(_describir(m)))
            fila.icono_paleta.set_visible(m["palette"])
            actual = Path(m["current"]) if m["current"] else None
            if actual != fila.miniatura.ruta:
                fila.miniatura.ruta = actual
                fila.miniatura.set_from_paintable(None)
                if actual:
                    self._pedir_miniatura(actual, lambda f, _o, img=fila.miniatura:
                                          img.set_from_paintable(_textura(f, 96, 54)))

    # --- coleccion

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
        if list(self.tarjetas) != fondos:
            self.cuadricula.remove_all()
            self.tarjetas = {}
            for path in fondos:
                tarjeta = Tarjeta(path)
                self.tarjetas[path] = tarjeta
                self.cuadricula.append(tarjeta)
                self._pedir_miniatura(path, lambda f, _o, t=tarjeta: t.cubierta.poner(_textura(f)))
        self._filtrar()
        self._marcar_actuales()
        return False

    def _pasa_filtro(self, tarjeta: Tarjeta) -> bool:
        filtro = self.filtro.get_active_name()
        if filtro == "imagen" and tarjeta.tipo != "imagen":
            return False
        if filtro == "animado" and tarjeta.tipo == "imagen":
            return False
        texto = self.busqueda.get_text().strip().lower()
        return not texto or texto in tarjeta.path.name.lower()

    def _filtrar(self) -> None:
        self.cuadricula.invalidate_filter()
        visibles = sum(1 for t in self.tarjetas.values() if self._pasa_filtro(t))
        total = len(self.tarjetas)
        animados = sum(1 for t in self.tarjetas.values() if t.tipo != "imagen")
        self.ruta_carpeta.set_label(f"{total} fondos · {animados} animados · {self.carpeta}")
        self.pila.set_visible_child_name("vacio" if not total else "fondos" if visibles else "nada")

    def _pedir_miniatura(self, path: Path, al_terminar, tamano=miniaturas.TARJETA) -> None:
        def hecho(futuro):
            fichero = None if futuro.cancelled() or futuro.exception() else futuro.result()
            GLib.idle_add(lambda: al_terminar(fichero, path) and False)
        try:
            futuro = self._hilos_miniaturas.submit(miniaturas.miniatura, path, tipo_de(path) != "imagen", tamano)
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

    def _abrir_carpeta(self) -> None:
        if self.carpeta:
            Gtk.FileLauncher(file=Gio.File.new_for_path(str(self.carpeta))).launch(self, None, None)


def _describir(m: dict) -> str:
    if not m["current"]:
        return "sin fondo"
    partes = [Path(m["current"]).name, NOMBRE_TIPO.get(m["kind"], "?").lower()]
    if m.get("backend") == "mpvpaper":
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
            "filtro": ("s", lambda _a, p: self.ventana.filtro.set_active_name(p.get_string())),
            "buscar": ("s", lambda _a, p: self.ventana.busqueda.set_text(p.get_string())),
            "abrir": (None, self._abrir),
            "conseguir": (None, lambda *_: self.ventana.boton_webs.popup()),
            "web": ("s", self._web),
            "ajustes": (None, self._ajustes),
            "acerca": (None, self._acerca),
            "arrancar-daemon": (None, self._arrancar_daemon),
            "salir": (None, lambda *_: self.quit()),
        }
        for nombre, (tipo, funcion) in acciones.items():
            accion = Gio.SimpleAction(name=nombre, parameter_type=GLib.VariantType(tipo) if tipo else None)
            accion.connect("activate", funcion)
            self.add_action(accion)
        self.set_accels_for_action("app.salir", ["<Control>q", "<Control>w"])
        self.set_accels_for_action("app.siguiente", ["<Control>Right"])
        self.set_accels_for_action("app.pausa", ["<Control>space"])

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

    def _web(self, _accion, parametro):
        url = parametro.get_string()

        def abierto(lanzador, resultado):
            try:
                lanzador.launch_finish(resultado)
            except GLib.Error as e:
                self.ventana.avisar(f"No pude abrir el navegador: {e.message}")
        Gtk.UriLauncher(uri=url).launch(self.ventana, None, abierto)

    def _ajustes(self, *_):
        from .ajustes import DialogoAjustes
        DialogoAjustes(self).present(self.ventana)

    def _acerca(self, *_):
        Adw.AboutDialog(
            application_name="NoHland", application_icon="preferences-desktop-wallpaper",
            version=__version__, developer_name="EricBatalla12",
            comments="Fondos de pantalla animados hechos a medida para Noctalia + Hyprland.",
            website="https://github.com/EricBatalla12/NoHland",
        ).present(self.ventana)

    def _arrancar_daemon(self, *_):
        self.reiniciar_daemon(arrancar=True)

    def reiniciar_daemon(self, arrancar: bool = False) -> None:
        verbo = "start" if arrancar else "restart"

        def al_terminar(r, error):
            if error or r.returncode:
                detalle = error or r.stderr.strip()
                self.ventana.avisar(f"systemctl --user {verbo} nohland falló: {detalle}")
            else:
                self.ventana.avisar("NoHland arrancado" if arrancar else "Ajustes aplicados; NoHland reiniciado")
            self.ventana._refrescar()
        en_hilo(lambda: subprocess.run(["systemctl", "--user", verbo, "nohland"],
                                       capture_output=True, text=True, timeout=30), al_terminar)


def main() -> int:
    return Aplicacion().run([])
