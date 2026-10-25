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

from . import __version__, actualizar, config, miniaturas  # noqa: E402
from .cliente import DaemonParado, enviar  # noqa: E402
from .config import ANIMATED_EXT, IMAGE_EXT  # noqa: E402

APP_ID = "io.github.EricBatalla12.NoHland"
# El icono se llama como el id oficial aunque la app corra con otro (pruebas)
ICONO = "io.github.EricBatalla12.NoHland"
# Atajos del tiempo de rotacion, en minutos
PRESETS_ROTACION = [(5, "5 min"), (15, "15 min"), (30, "30 min"), (60, "1 h"), (180, "3 h")]
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
.rotacion { padding: 6px 6px 6px 14px; border-radius: 14px; background: alpha(@window_fg_color, 0.06); }
.titulo-app { font-weight: 800; }
.boton-tarjeta { margin: 8px; min-width: 34px; min-height: 34px; padding: 0; border-radius: 999px;
                 background: alpha(black, 0.55); color: white; opacity: 0; transition: opacity 150ms ease; }
flowboxchild:hover .boton-tarjeta, .boton-tarjeta:checked, .boton-tarjeta:focus-visible { opacity: 1; }
.boton-tarjeta > button { min-width: 34px; min-height: 34px; padding: 0; border-radius: 999px;
                          background: none; color: white; }
.marca-rotacion { margin: 10px; padding: 6px; border-radius: 999px; background: alpha(black, 0.6); color: white; }
.marca-rotacion.dentro { background: @accent_bg_color; color: @accent_fg_color; }
.tarjeta.fuera-de-rotacion { opacity: 0.42; }
.barra-seleccion { padding: 10px 10px 10px 16px; border-radius: 16px; background: alpha(@accent_bg_color, 0.16); }
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
        self._tipo_texto = f"{NOMBRE_TIPO[self.tipo]} · {path.suffix.lstrip('.').upper()}"
        self.etiqueta_tipo = _etiqueta(self._tipo_texto, "tarjeta-tipo", xalign=0)
        velo.append(self.etiqueta_tipo)
        caja.add_overlay(velo)

        # Menu de la tarjeta: boton que aparece al pasar el raton, o clic derecho
        self.boton_menu = Gtk.MenuButton(icon_name="view-more-symbolic", halign=Gtk.Align.END,
                                         valign=Gtk.Align.END, tooltip_text="Más opciones")
        self.boton_menu.add_css_class("boton-tarjeta")
        caja.add_overlay(self.boton_menu)
        clic_derecho = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        clic_derecho.connect("pressed", lambda *_: self.boton_menu.popup())
        self.add_controller(clic_derecho)

        # En el modo «elegir fondos de la rotacion»: dentro o fuera
        self.marca = Gtk.Image(icon_name="object-select-symbolic", pixel_size=16, visible=False,
                               halign=Gtk.Align.END, valign=Gtk.Align.END)
        self.marca.add_css_class("marca-rotacion")
        caja.add_overlay(self.marca)

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

    def marcar_rotacion(self, dentro: bool, eligiendo: bool) -> None:
        self.en_rotacion = dentro
        self.marca.set_visible(eligiendo)
        self.boton_menu.set_visible(not eligiendo)
        self.marca.set_from_icon_name("object-select-symbolic" if dentro else "list-remove-symbolic")
        if dentro:
            self.marca.add_css_class("dentro")
        else:
            self.marca.remove_css_class("dentro")
        (self._caja.add_css_class if eligiendo and not dentro else self._caja.remove_css_class)("fuera-de-rotacion")
        self.etiqueta_tipo.set_label(self._tipo_texto + ("" if dentro else " · fuera de la rotación"))


# ------------------------------------------------------------ rotacion

class PopoverRotacion(Gtk.Popover):
    """Tiempo, orden y fondos de la rotacion automatica."""

    def __init__(self, ventana: "Ventana") -> None:
        super().__init__()
        self.ventana = ventana
        self._pintando = False
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, width_request=380,
                       margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)
        caja.append(_etiqueta("Rotación automática", "heading", xalign=0))

        caja.append(_etiqueta("Cambiar cada", "caption-heading", xalign=0, margin_top=4))
        self.presets = Adw.ToggleGroup()
        for minutos, texto in PRESETS_ROTACION:
            self.presets.add(Adw.Toggle(name=str(minutos), label=texto))
        self.presets.connect("notify::active-name", self._preset_elegido)
        caja.append(self.presets)
        otro = Gtk.Box(spacing=8)
        otro.append(_etiqueta("O exactamente", "dim-label", hexpand=True, xalign=0))
        self.minutos = Gtk.SpinButton.new_with_range(1, 7 * 24 * 60, 1)
        self.minutos.connect("value-changed", self._minutos_cambiados)
        otro.append(self.minutos)
        otro.append(_etiqueta("min", "dim-label"))
        caja.append(otro)

        caja.append(_etiqueta("Orden", "caption-heading", xalign=0, margin_top=4))
        self.orden = Adw.ToggleGroup()
        self.orden.add(Adw.Toggle(name="orden", label="En orden", icon_name="view-list-symbolic"))
        self.orden.add(Adw.Toggle(name="aleatorio", label="Aleatorio", icon_name="media-playlist-shuffle-symbolic"))
        self.orden.connect("notify::active-name", self._orden_elegido)
        caja.append(self.orden)

        caja.append(_etiqueta("Fondos", "caption-heading", xalign=0, margin_top=4))
        fila = Gtk.Box(spacing=8)
        self.cuenta = _etiqueta("", "dim-label", hexpand=True, xalign=0, wrap=True)
        fila.append(self.cuenta)
        elegir = Gtk.Button(label="Elegir…")
        elegir.connect("clicked", lambda *_: (self.popdown(),
                                              ventana.app.activate_action("rotacion-elegir", GLib.Variant("b", True))))
        fila.append(elegir)
        caja.append(fila)
        self.set_child(caja)
        self._debounce = 0

    def pintar(self, r: dict, dentro: int, total: int) -> None:
        self._pintando = True
        minutos = r["interval_minutes"]
        nombre = str(int(minutos)) if float(minutos).is_integer() else ""
        self.presets.set_active_name(nombre if any(str(m) == nombre for m, _ in PRESETS_ROTACION) else None)
        if not self.minutos.has_focus():
            self.minutos.set_value(minutos)
        self.orden.set_active_name(r.get("order", "orden"))
        self.cuenta.set_label(f"{dentro} de {total} fondos de esta carpeta entran en la rotación")
        self._pintando = False

    def _preset_elegido(self, *_):
        nombre = self.presets.get_active_name()
        if self._pintando or not nombre:
            return
        self.ventana.app.activate_action("rotacion-minutos", GLib.Variant("d", float(nombre)))

    def _minutos_cambiados(self, boton):
        if self._pintando:
            return
        # Se espera a que deje de pulsar las flechas para mandar una sola orden
        if self._debounce:
            GLib.source_remove(self._debounce)
        def enviar():
            self._debounce = 0
            self.ventana.app.activate_action("rotacion-minutos", GLib.Variant("d", boton.get_value()))
            return False
        self._debounce = GLib.timeout_add(700, enviar)

    def _orden_elegido(self, *_):
        nombre = self.orden.get_active_name()
        if not self._pintando and nombre:
            self.ventana.app.activate_action("rotacion-orden", GLib.Variant("s", nombre))


# ------------------------------------------------------------ colores y greeter

# Generadores de paleta de Noctalia (docs.noctalia.dev, theming), con un nombre claro
ESTILOS_PALETA = [
    ("m3-content", "Fiel al fondo (m3-content)"),
    ("m3-tonal-spot", "Suave (m3-tonal-spot)"),
    ("vibrant", "Vivo (vibrant)"),
    ("m3-fruit-salad", "Variado (m3-fruit-salad)"),
    ("m3-rainbow", "Arcoíris (m3-rainbow)"),
    ("faithful", "Exacto (faithful)"),
    ("soft", "Pastel (soft)"),
    ("muted", "Apagado (muted)"),
    ("m3-monochrome", "Monocromo (m3-monochrome)"),
]


def esquema_noctalia() -> tuple[str, str]:
    """(fuente, nombre) de la paleta de Noctalia, p. ej. ("wallpaper", "m3-content")."""
    r = subprocess.run(["noctalia", "msg", "color-scheme-get"], capture_output=True, text=True, timeout=10)
    partes = r.stdout.split()
    if r.returncode != 0 or len(partes) < 2:
        raise RuntimeError(r.stdout.strip() or r.stderr.strip() or "Noctalia no responde")
    return partes[0], partes[1]


class PopoverEscritorio(Gtk.Popover):
    """Colores del escritorio (paleta de Noctalia) y fondo del inicio de sesion."""

    def __init__(self, ventana: "Ventana") -> None:
        super().__init__()
        self.ventana = ventana
        self._pintando = False
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, width_request=420,
                       margin_top=14, margin_bottom=14, margin_start=14, margin_end=14)

        # --- colores
        caja.append(_etiqueta("Colores del escritorio", "heading", xalign=0))
        fila = Gtk.Box(spacing=10)
        textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        textos.append(_etiqueta("Los colores siguen al fondo", xalign=0))
        self.info_colores = _etiqueta("", "dim-label", "caption", xalign=0, wrap=True, max_width_chars=44)
        textos.append(self.info_colores)
        fila.append(textos)
        self.colores = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.colores.connect("notify::active", self._colores_cambiados)
        fila.append(self.colores)
        caja.append(fila)
        fila = Gtk.Box(spacing=10)
        fila.append(_etiqueta("Estilo", "dim-label", xalign=0, hexpand=True))
        self.estilo = Gtk.DropDown(model=Gtk.StringList.new([t for _, t in ESTILOS_PALETA]))
        self.estilo.connect("notify::selected", self._estilo_cambiado)
        fila.append(self.estilo)
        caja.append(fila)
        # Abre los ajustes de Noctalia en «Plantillas»: una casilla por app
        # (GTK, Qt, kitty, btop, bordes de Hyprland...) que cambia de color
        mas = Gtk.Button(label="Elegir qué apps cambian de color…", halign=Gtk.Align.START,
                         tooltip_text="Abre las plantillas de Noctalia: una casilla por aplicación")
        mas.add_css_class("flat")
        mas.connect("clicked", lambda *_: (self.popdown(), ventana.app.activate_action("noctalia-ajustes", None)))
        caja.append(mas)

        caja.append(Gtk.Separator(margin_top=4, margin_bottom=4))

        # --- greeter
        caja.append(_etiqueta("Pantalla de inicio de sesión", "heading", xalign=0))
        self.modo_greeter = Adw.ToggleGroup()
        for nombre, texto in (("escritorio", "Como el escritorio"), ("fija", "Imagen fija"), ("no", "No tocarla")):
            self.modo_greeter.add(Adw.Toggle(name=nombre, label=texto))
        self.modo_greeter.connect("notify::active-name", self._modo_greeter_cambiado)
        caja.append(self.modo_greeter)
        fila = Gtk.Box(spacing=10)
        self.mini_greeter = Gtk.Image(pixel_size=96)
        fila.append(self.mini_greeter)
        self.info_greeter = _etiqueta("", "dim-label", "caption", xalign=0, wrap=True, hexpand=True,
                                      max_width_chars=30, selectable=True)
        fila.append(self.info_greeter)
        self.elegir_greeter = Gtk.Button(label="Elegir…", valign=Gtk.Align.CENTER)
        self.elegir_greeter.connect("clicked", lambda *_: self._elegir_imagen())
        fila.append(self.elegir_greeter)
        caja.append(fila)
        self._mini_ruta = None
        self.set_child(caja)
        self.connect("show", lambda *_: self.leer_colores())

    # --- colores

    def leer_colores(self) -> None:
        def hecho(r, error):
            self._pintando = True
            if error:
                self.colores.set_sensitive(False)
                self.estilo.set_sensitive(False)
                self.info_colores.set_label(f"No pude preguntar a Noctalia: {error}")
            else:
                fuente, nombre = r
                activo = fuente == "wallpaper"
                self.colores.set_sensitive(True)
                self.colores.set_active(activo)
                self.estilo.set_sensitive(activo)
                valores = [v for v, _ in ESTILOS_PALETA]
                if activo and nombre in valores:
                    self.estilo.set_selected(valores.index(nombre))
                self.info_colores.set_label(
                    "Noctalia saca la paleta del fondo; con vídeos, de un fotograma" if activo
                    else f"Ahora Noctalia usa una paleta fija ({fuente}: {nombre})")
            self._pintando = False
        en_hilo(esquema_noctalia, hecho)

    def _colores_cambiados(self, interruptor, _p):
        if not self._pintando:
            self.ventana.app.activate_action("colores", GLib.Variant("b", interruptor.get_active()))

    def _estilo_cambiado(self, *_):
        if not self._pintando and self.colores.get_active():
            estilo = ESTILOS_PALETA[self.estilo.get_selected()][0]
            self.ventana.app.activate_action("colores-estilo", GLib.Variant("s", estilo))

    # --- greeter

    def pintar_greeter(self, g: dict | None) -> None:
        self._pintando = True
        disponible = bool(g and g["available"])
        self.modo_greeter.set_sensitive(disponible)
        if g:
            self.modo_greeter.set_active_name(g["mode"])
        self.elegir_greeter.set_visible(disponible and g["mode"] == "fija")
        self._pintando = False
        if not g:
            self.info_greeter.set_label("NoHland está parado")
        elif not disponible:
            self.info_greeter.set_label("Falta prepararlo una vez, con contraseña:\n"
                                        "sudo ~/Proyectos/NoHland/tools/preparar-greeter.sh")
        elif g["mode"] == "no":
            self.info_greeter.set_label("NoHland no cambia el fondo del inicio de sesión")
        elif g["mode"] == "fija":
            self.info_greeter.set_label(Path(g["image"]).name if g["image"] else "Elige una imagen o vídeo")
        else:
            self.info_greeter.set_label("El mismo fondo que el escritorio (con vídeos, un fotograma). "
                                        "Se verá al iniciar sesión")
        ruta = g.get("last") if g and disponible and g["mode"] != "no" else None
        if ruta != self._mini_ruta:
            self._mini_ruta = ruta
            self.mini_greeter.set_from_paintable(None)
            if ruta:
                self.ventana._pedir_miniatura(Path(ruta), lambda f, _o: self.mini_greeter.set_from_paintable(
                    _textura(f, 96, 54)))
        self.mini_greeter.set_visible(bool(ruta))

    def _modo_greeter_cambiado(self, *_):
        modo = self.modo_greeter.get_active_name()
        if not self._pintando and modo:
            self.ventana.app.activate_action("greeter-modo", GLib.Variant("s", modo))

    def _elegir_imagen(self):
        self.popdown()
        self.ventana.app.elegir_archivo("Imagen para el inicio de sesión",
                                        lambda ruta: self.ventana.app.activate_action(
                                            "greeter-imagen", GLib.Variant("s", ruta)))


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
        # Con fondo propio del workspace: volver al general del monitor
        self.usar_general = Gtk.Button(label="Usar el fondo general", action_name="app.workspace-general",
                                       halign=Gtk.Align.START, visible=False,
                                       tooltip_text="Este workspace deja de tener fondo propio")
        self.usar_general.add_css_class("chip")
        self.usar_general.add_css_class("flat")
        textos.append(self.etiqueta)
        textos.append(self.titulo)
        textos.append(self.chips)
        textos.append(self.usar_general)
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
                hay_video: bool = False, rotacion: dict | None = None) -> None:
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
            if rotacion and rotacion["enabled"] and rotacion["next_in_s"] is not None:
                if monitor["name"] in rotacion["pending"]:
                    chips.append(("Cambio pendiente: espera a que se quite la pausa", ""))
                else:
                    chips.append((f"Cambia en {_duracion(rotacion['next_in_s'])}", ""))
            if monitor.get("workspace_own"):
                chips.append((f"Fondo propio del workspace {monitor['workspace']}", "acento"))
            if monitor.get("palette") and varios:
                chips.append(("Paleta de Noctalia", ""))
            if monitor.get("error"):
                chips.append((monitor["error"], "pausa"))
            self._poner_chips(chips)
            self.usar_general.set_visible(bool(monitor.get("workspace_own")))
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
        self.usar_general.set_visible(False)
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
        marca = Gtk.Box(spacing=8)
        marca.append(Gtk.Image(icon_name=ICONO, pixel_size=22))
        marca.append(_etiqueta("NoHland", "titulo-app"))
        cabecera = Adw.HeaderBar(title_widget=marca)
        self.selector_monitor = Adw.ToggleGroup(visible=False, tooltip_text="Monitor al que van los cambios")
        self.selector_monitor.connect("notify::active-name", lambda *_: self._monitor_cambiado())
        cabecera.pack_start(self.selector_monitor)

        menu = Gio.Menu()
        menu.append("Ajustes", "app.ajustes")
        menu.append("Buscar actualizaciones", "app.buscar-actualizacion")
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
        self.popover_escritorio = PopoverEscritorio(self)
        cabecera.pack_end(Gtk.MenuButton(popover=self.popover_escritorio,
                                         icon_name="preferences-desktop-appearance-symbolic",
                                         tooltip_text="Colores del escritorio e inicio de sesión"))

        self.aviso = Adw.Banner(title="El daemon de NoHland no está en marcha", button_label="Arrancar")
        self.aviso.set_action_name("app.arrancar-daemon")
        self.aviso_version = Adw.Banner(button_label="Ver y actualizar")
        self.aviso_version.set_action_name("app.ver-actualizacion")
        self.actualizacion: dict | None = None

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

        # Interruptor de la rotacion automatica
        rotacion = Gtk.Box(spacing=10, valign=Gtk.Align.CENTER)
        rotacion.add_css_class("rotacion")
        rotacion.append(Gtk.Image(icon_name="media-playlist-repeat-symbolic"))
        textos_rot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        textos_rot.append(_etiqueta("Rotación", "heading", xalign=0))
        self.rotacion_info = _etiqueta("", "dim-label", "caption", xalign=0)
        textos_rot.append(self.rotacion_info)
        rotacion.append(textos_rot)
        self.rotacion = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Cambiar de fondo solo cada cierto tiempo")
        self._pintando_rotacion = False
        self.rotacion.connect("notify::active", self._rotacion_cambiada)
        rotacion.append(self.rotacion)
        self.popover_rotacion = PopoverRotacion(self)
        self.boton_rotacion = Gtk.MenuButton(popover=self.popover_rotacion, icon_name="pan-down-symbolic",
                                             valign=Gtk.Align.CENTER, tooltip_text="Tiempo, orden y fondos de la rotación")
        self.boton_rotacion.add_css_class("flat")
        rotacion.append(self.boton_rotacion)

        barra = Gtk.Box(spacing=12)
        for w in (titulo, rotacion, self.filtro, self.busqueda, carpeta):
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

        # Modo «elegir fondos de la rotacion»: un clic mete o saca la tarjeta
        self.eligiendo = False
        seleccion = Gtk.Box(spacing=8)
        seleccion.add_css_class("barra-seleccion")
        seleccion.append(Gtk.Image(icon_name="media-playlist-repeat-symbolic"))
        self.texto_seleccion = _etiqueta("", hexpand=True, xalign=0, wrap=True)
        seleccion.append(self.texto_seleccion)
        for texto, valor in (("Todos", True), ("Ninguno", False)):
            b = Gtk.Button(label=texto, action_name="app.rotacion-todos", action_target=GLib.Variant("b", valor))
            b.add_css_class("flat")
            seleccion.append(b)
        listo = Gtk.Button(label="Listo", action_name="app.rotacion-elegir", action_target=GLib.Variant("b", False))
        listo.add_css_class("suggested-action")
        seleccion.append(listo)
        self.revelar_seleccion = Gtk.Revealer(child=seleccion, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22,
                       margin_top=8, margin_bottom=32, margin_start=28, margin_end=28)
        for w in (self.portada, self.lista_monitores, barra, self.revelar_seleccion, self.pila):
            caja.append(w)
        desplazable = Gtk.ScrolledWindow(child=Adw.Clamp(child=caja, maximum_size=1600, tightening_threshold=1200),
                                         vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)

        vista = Adw.ToolbarView()
        vista.add_top_bar(cabecera)
        vista.add_top_bar(self.aviso)
        vista.add_top_bar(self.aviso_version)
        vista.set_content(desplazable)
        self.toasts = Adw.ToastOverlay(child=vista)
        self.set_content(self.toasts)

        atajo = Gtk.ShortcutController()
        atajo.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Control>f"),
                                        action=Gtk.CallbackAction.new(lambda *_: self.busqueda.grab_focus() or True)))
        self.add_controller(atajo)

        self._refrescar()
        if config.load()[0].check_updates:
            self.buscar_actualizacion(forzar=False)
        self._temporizador = GLib.timeout_add_seconds(REFRESCO_S, self._refrescar)
        self.connect("close-request", self._al_cerrar)

    def _al_cerrar(self, *_):
        GLib.source_remove(self._temporizador)
        self._hilos_miniaturas.shutdown(wait=False, cancel_futures=True)
        return False

    def buscar_actualizacion(self, forzar: bool) -> None:
        def hecho(r, error):
            if error:
                r = {"estado": "error", "nuevos": [], "detalle": str(error)}
            self.actualizacion = r
            hay = r["estado"] in ("nueva", "cambios-locales")
            if hay:
                n = len(r["nuevos"])
                self.aviso_version.set_title(f"Hay una versión nueva de NoHland ({n} cambio{'s' if n != 1 else ''})")
            self.aviso_version.set_revealed(hay)
            if forzar and not hay:
                self.avisar({"al-dia": "Ya tienes la última versión",
                             "por-delante": "Tu copia va por delante de GitHub (estás desarrollando)",
                             "no-git": "NoHland no está instalado desde una copia de git",
                             }.get(r["estado"], f"No pude consultar GitHub: {r['detalle']}"))
        en_hilo(lambda: actualizar.comprobar(forzar), hecho)

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
            self._pintar_rotacion(None)
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
        self.portada.mostrar(portada, len(nombres) > 1, e["manual_pause"], sin_daemon=False,
                             hay_video=hay_video, rotacion=e.get("rotation"))
        self._pintar_rotacion(e.get("rotation"))
        self.popover_escritorio.pintar_greeter(e.get("greeter"))
        # Se pide una vez por fondo; si ffmpeg falla no se reintenta cada 2 s
        if portada and portada["current"] and self.portada.ruta != self._portada_pedida:
            self._portada_pedida = self.portada.ruta
            self._pedir_miniatura(self.portada.ruta, self._portada_lista, miniaturas.PORTADA)

        self._pintar_monitores(e["monitors"] if len(nombres) > 1 else [])
        self._actualizar_carpeta()
        self._marcar_actuales()

    def _pintar_rotacion(self, r: dict | None) -> None:
        self.rotacion.set_sensitive(r is not None)
        self.boton_rotacion.set_sensitive(r is not None)
        if r is None:
            self.rotacion_info.set_label("NoHland parado")
            return
        # Se marca para que poner el interruptor desde el estado no mande otra orden
        self._pintando_rotacion = True
        self.rotacion.set_active(r["enabled"])
        self._pintando_rotacion = False
        excluidos = set(r.get("excluded", []))
        for path, tarjeta in self.tarjetas.items():
            tarjeta.marcar_rotacion(str(path) not in excluidos, self.eligiendo)
        dentro = sum(1 for p in self.tarjetas if str(p) not in excluidos)
        self.popover_rotacion.pintar(r, dentro, len(self.tarjetas))
        self.texto_seleccion.set_label(f"Toca los fondos para meterlos o sacarlos de la rotación · "
                                       f"{dentro} de {len(self.tarjetas)} dentro")
        cada = _duracion(r["interval_minutes"] * 60)
        if dentro == 0 and r["enabled"]:
            self.rotacion_info.set_label("Ningún fondo en la rotación")
        elif not r["enabled"]:
            self.rotacion_info.set_label(f"Apagada · cada {cada}")
        elif r["pending"]:
            self.rotacion_info.set_label("Esperando: hay algo en pausa")
        else:
            self.rotacion_info.set_label(f"Cada {cada} · siguiente en {_duracion(r['next_in_s'] or 0)}")

    def _rotacion_cambiada(self, interruptor, _param) -> None:
        if not self._pintando_rotacion:
            self.app.activate_action("rotacion", GLib.Variant("b", interruptor.get_active()))

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
                tarjeta.boton_menu.set_create_popup_func(
                    lambda boton, t=tarjeta: boton.set_menu_model(self.menu_tarjeta(t)))
                self.tarjetas[path] = tarjeta
                self.cuadricula.append(tarjeta)
                self._pedir_miniatura(path, lambda f, _o, t=tarjeta: t.cubierta.poner(_textura(f)))
        self._filtrar()
        self._marcar_actuales()
        if self.estado:
            self._pintar_rotacion(self.estado.get("rotation"))
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
        if self.eligiendo:
            self.app.activate_action("rotacion-alternar", GLib.Variant("s", str(tarjeta.path)))
        else:
            self.app.activate_action("poner", GLib.Variant("s", str(tarjeta.path)))

    def elegir_rotacion(self, activo: bool) -> None:
        self.eligiendo = activo
        self.revelar_seleccion.set_reveal_child(activo)
        if self.estado:
            self._pintar_rotacion(self.estado.get("rotation"))

    def menu_tarjeta(self, tarjeta: Tarjeta) -> Gio.Menu:
        """El menu se construye al abrirlo: depende de los monitores y de la rotacion."""
        ruta = str(tarjeta.path)
        menu = Gio.Menu()
        poner = Gio.Menu()
        destinos = [("", "Ponerlo en todos los monitores" if len(self.monitores) > 1 else "Ponerlo de fondo")]
        if len(self.monitores) > 1:
            destinos += [(m, f"Ponerlo solo en {m}") for m in self.monitores]
        for monitor, texto in destinos:
            item = Gio.MenuItem.new(texto, None)
            item.set_action_and_target_value("app.poner-en", GLib.Variant("(ss)", (monitor, ruta)))
            poner.append_item(item)
        # Solo en el workspace que se ve (en el monitor elegido o, con «Todos»,
        # en el de la paleta)
        estado = self.estado or {}
        ref = next((m for m in estado.get("monitors", []) if m["name"] == self.monitor_elegido()), None) or \
            next((m for m in estado.get("monitors", []) if m["palette"]), None)
        if ref and ref.get("workspace"):
            item = Gio.MenuItem.new(f"Ponerlo solo en el workspace {ref['workspace']}", None)
            item.set_action_and_target_value("app.poner-workspace", GLib.Variant("s", ruta))
            poner.append_item(item)
        # Con fondo propio en el workspace, poner un fondo cambia el propio; esto
        # cambia el general (el de los workspaces sin fondo propio)
        if any(m.get("workspace_own") for m in estado.get("monitors", [])):
            item = Gio.MenuItem.new("Ponerlo de fondo general", None)
            item.set_action_and_target_value("app.poner-general", GLib.Variant("s", ruta))
            poner.append_item(item)
        menu.append_section(None, poner)
        otros = Gio.Menu()
        dentro = getattr(tarjeta, "en_rotacion", True)
        item = Gio.MenuItem.new("Sacar de la rotación" if dentro else "Meter en la rotación", None)
        item.set_action_and_target_value("app.rotacion-alternar", GLib.Variant("s", ruta))
        otros.append_item(item)
        item = Gio.MenuItem.new("Usar en el inicio de sesión", None)
        item.set_action_and_target_value("app.greeter-imagen", GLib.Variant("s", ruta))
        otros.append_item(item)
        item = Gio.MenuItem.new("Mostrar en la carpeta", None)
        item.set_action_and_target_value("app.mostrar", GLib.Variant("s", ruta))
        otros.append_item(item)
        menu.append_section(None, otros)
        papelera = Gio.Menu()
        item = Gio.MenuItem.new("Mover a la papelera…", None)
        item.set_action_and_target_value("app.papelera", GLib.Variant("s", ruta))
        papelera.append_item(item)
        menu.append_section(None, papelera)
        return menu

    def _abrir_carpeta(self) -> None:
        if self.carpeta:
            Gtk.FileLauncher(file=Gio.File.new_for_path(str(self.carpeta))).launch(self, None, None)


def _duracion(segundos: float) -> str:
    segundos = max(0, round(segundos))
    if segundos < 60:
        return f"{segundos} s"
    minutos = round(segundos / 60)
    if minutos < 60:
        return f"{minutos} min"
    h, m = divmod(minutos, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def _noctalia(*args: str) -> None:
    r = subprocess.run(["noctalia", "msg", *args], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        raise RuntimeError(r.stdout.strip() or r.stderr.strip() or f"noctalia msg {args[0]} falló")


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
            "rotacion": ("b", self._rotacion),
            "rotacion-minutos": ("d", lambda _a, p: self.orden(
                {"cmd": "rotate", "minutes": p.get_double()}, f"La rotación cambia cada {_duracion(p.get_double() * 60)}")),
            "rotacion-orden": ("s", lambda _a, p: self.orden(
                {"cmd": "rotate", "order": p.get_string()},
                "Rotación aleatoria" if p.get_string() == "aleatorio" else "Rotación en orden")),
            "rotacion-alternar": ("s", self._rotacion_alternar),
            "rotacion-todos": ("b", self._rotacion_todos),
            "rotacion-elegir": ("b", lambda _a, p: self.ventana.elegir_rotacion(p.get_boolean())),
            "poner-en": ("(ss)", self._poner_en),
            "poner-workspace": ("s", self._poner_workspace),
            "poner-general": ("s", self._poner_general),
            "workspace-general": (None, lambda *_: self.orden(
                self._con_monitor({"cmd": "workspace-clear"}), "El workspace vuelve al fondo general")),
            "mostrar": ("s", self._mostrar),
            "papelera": ("s", self._papelera),
            "colores": ("b", self._colores),
            "colores-estilo": ("s", self._colores_estilo),
            "noctalia-ajustes": (None, lambda *_: subprocess.Popen(
                ["noctalia", "msg", "settings-open", "templates"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)),
            "greeter-modo": ("s", lambda _a, p: self.orden(
                {"cmd": "greeter", "mode": p.get_string()}, "Inicio de sesión: " + {
                    "escritorio": "como el escritorio", "fija": "imagen fija", "no": "no se toca"}[p.get_string()])),
            "greeter-imagen": ("s", lambda _a, p: self.orden(
                {"cmd": "greeter", "mode": "fija", "image": p.get_string()},
                f"{Path(p.get_string()).name} en el inicio de sesión")),
            "abrir": (None, self._abrir),
            "conseguir": (None, lambda *_: self.ventana.boton_webs.popup()),
            "web": ("s", self._web),
            "ajustes": (None, self._ajustes),
            "acerca": (None, self._acerca),
            "buscar-actualizacion": (None, lambda *_: self.ventana.buscar_actualizacion(forzar=True)),
            "ver-actualizacion": (None, self._ver_actualizacion),
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
        Gtk.Window.set_default_icon_name(ICONO)
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
        self.elegir_archivo("Elegir fondo", lambda ruta: self.activate_action("poner", GLib.Variant("s", ruta)))

    def _rotacion_alternar(self, _accion, parametro):
        ruta = parametro.get_string()
        excluidos = set((self.ventana.estado or {}).get("rotation", {}).get("excluded", []))
        if ruta in excluidos:
            self.orden({"cmd": "rotate", "include": [ruta]}, "")
        else:
            self.orden({"cmd": "rotate", "exclude": [ruta]}, "")

    def _rotacion_todos(self, _accion, parametro):
        rutas = [str(p) for p in self.ventana.tarjetas]
        if parametro.get_boolean():
            self.orden({"cmd": "rotate", "include": rutas}, "Todos los fondos en la rotación")
        else:
            self.orden({"cmd": "rotate", "exclude": rutas}, "Ningún fondo en la rotación")

    def _poner_en(self, _accion, parametro):
        monitor, ruta = parametro.unpack()
        peticion = {"cmd": "set", "path": ruta}
        if monitor:
            peticion["monitor"] = monitor
        self.ventana.avisar(f"Poniendo {Path(ruta).name}…")
        self.orden(peticion, f"{Path(ruta).name} en {monitor or 'todos los monitores'}")

    def _poner_workspace(self, _accion, parametro):
        ruta = parametro.get_string()
        self.ventana.avisar(f"Poniendo {Path(ruta).name}…")
        self.orden(self._con_monitor({"cmd": "set", "path": ruta, "workspace": True}),
                   f"{Path(ruta).name}: fondo propio de este workspace")

    def _poner_general(self, _accion, parametro):
        ruta = parametro.get_string()
        self.ventana.avisar(f"Poniendo {Path(ruta).name}…")
        self.orden(self._con_monitor({"cmd": "set", "path": ruta, "general": True}),
                   f"{Path(ruta).name}: fondo general")

    def _mostrar(self, _accion, parametro):
        Gtk.FileLauncher(file=Gio.File.new_for_path(parametro.get_string())).open_containing_folder(
            self.ventana, None, None)

    def _colores(self, _accion, parametro):
        activar = parametro.get_boolean()

        def cambiar():
            if activar:
                fuente, nombre = esquema_noctalia()
                estilo = nombre if fuente == "wallpaper" else "m3-content"
                _noctalia("color-scheme-set", "wallpaper", estilo)
            else:
                # Vuelve a la paleta fija que tuviera elegida en Noctalia
                from .doctor import _toml_noctalia
                fija = _toml_noctalia().get("theme", {}).get("builtin") or "Noctalia"
                _noctalia("color-scheme-set", "builtin", fija)
                return fija

        def hecho(r, error):
            if error:
                self.ventana.avisar(f"No pude cambiar los colores: {error}")
            elif activar:
                # Noctalia necesita un wallpaper-set para tener de donde sacarla
                self.orden({"cmd": "palette"}, "Los colores siguen al fondo")
            else:
                self.ventana.avisar(f"Colores fijos: paleta «{r}» de Noctalia")
            self.ventana.popover_escritorio.leer_colores()
        en_hilo(cambiar, hecho)

    def _colores_estilo(self, _accion, parametro):
        estilo = parametro.get_string()

        def hecho(_r, error):
            if error:
                self.ventana.avisar(f"No pude cambiar el estilo: {error}")
            else:
                self.orden({"cmd": "palette"}, f"Estilo de colores: {estilo}")
            self.ventana.popover_escritorio.leer_colores()
        en_hilo(lambda: _noctalia("color-scheme-set", "wallpaper", estilo), hecho)

    def elegir_archivo(self, titulo: str, al_elegir) -> None:
        filtro = Gtk.FileFilter(name="Fondos (imágenes, vídeos y GIF)")
        for ext in sorted(IMAGE_EXT | ANIMATED_EXT):
            filtro.add_suffix(ext.lstrip("."))
        filtros = Gio.ListStore.new(Gtk.FileFilter)
        filtros.append(filtro)
        dialogo = Gtk.FileDialog(title=titulo, filters=filtros, default_filter=filtro)
        if self.ventana.carpeta:
            dialogo.set_initial_folder(Gio.File.new_for_path(str(self.ventana.carpeta)))

        def elegido(d, resultado):
            try:
                fichero = d.open_finish(resultado)
            except GLib.Error:
                return  # cancelado
            al_elegir(fichero.get_path())
        dialogo.open(self.ventana, None, elegido)

    def _papelera(self, _accion, parametro):
        ruta = Path(parametro.get_string())
        dialogo = Adw.AlertDialog(heading="¿Mover a la papelera?",
                                  body=f"«{ruta.name}» saldrá de la colección. Puedes recuperarlo desde la papelera.")
        dialogo.add_response("cancelar", "Cancelar")
        dialogo.add_response("papelera", "Mover a la papelera")
        dialogo.set_response_appearance("papelera", Adw.ResponseAppearance.DESTRUCTIVE)
        dialogo.set_default_response("cancelar")
        dialogo.set_close_response("cancelar")

        def respuesta(_d, r):
            if r != "papelera":
                return
            try:
                Gio.File.new_for_path(str(ruta)).trash(None)
                self.ventana.avisar(f"{ruta.name} movido a la papelera")
            except GLib.Error as e:
                self.ventana.avisar(f"No pude moverlo a la papelera: {e.message}")
        dialogo.connect("response", respuesta)
        dialogo.present(self.ventana)

    def _rotacion(self, _accion, parametro):
        encendida = parametro.get_boolean()
        self.orden({"cmd": "rotate", "enabled": encendida},
                   "Rotación encendida" if encendida else "Rotación apagada")

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

    def _ver_actualizacion(self, *_):
        r = self.ventana.actualizacion or {"estado": "al-dia", "nuevos": []}
        novedades = "\n".join(f"• {titulo}" for _sha, titulo in r["nuevos"][:12])
        if len(r["nuevos"]) > 12:
            novedades += f"\n• …y {len(r['nuevos']) - 12} más"
        dialogo = Adw.AlertDialog(heading="Novedades de NoHland", body=novedades or "Sin cambios")
        dialogo.add_response("cerrar", "Ahora no")
        if r["estado"] == "nueva":
            dialogo.add_response("actualizar", "Actualizar")
            dialogo.set_response_appearance("actualizar", Adw.ResponseAppearance.SUGGESTED)
            dialogo.set_default_response("actualizar")
        elif r["estado"] == "cambios-locales":
            dialogo.set_body(dialogo.get_body() + "\n\nTu copia tiene cambios sin guardar, así que no la toco: "
                             f"actualiza a mano con git en {actualizar.REPO}.")
        dialogo.set_close_response("cerrar")
        dialogo.connect("response", lambda _d, resp: resp == "actualizar" and self._actualizar())
        dialogo.present(self.ventana)

    def _actualizar(self):
        self.ventana.avisar("Actualizando NoHland…")
        self.ventana.aviso_version.set_revealed(False)

        def hecho(resumen, error):
            if error:
                self.ventana.avisar(f"No pude actualizar: {error}")
                return
            fin = Adw.AlertDialog(heading="NoHland actualizado",
                                  body=f"Versión {resumen}. El servicio ya se ha reiniciado; "
                                       "falta reabrir esta ventana para ver lo nuevo.")
            fin.add_response("luego", "Luego")
            fin.add_response("reabrir", "Reabrir ahora")
            fin.set_response_appearance("reabrir", Adw.ResponseAppearance.SUGGESTED)
            fin.set_default_response("reabrir")
            fin.connect("response", lambda _d, resp: resp == "reabrir" and self._reabrir())
            fin.present(self.ventana)
        en_hilo(actualizar.actualizar, hecho)

    def _reabrir(self):
        # La app es de instancia unica: la nueva se lanza con un segundo de
        # retraso para que esta haya soltado su nombre en D-Bus
        subprocess.Popen(["sh", "-c", f"sleep 1; exec {actualizar.REPO / 'bin' / 'nohland'} gui"],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.quit()

    def _acerca(self, *_):
        Adw.AboutDialog(
            application_name="NoHland", application_icon=ICONO,
            version=__version__, developer_name="EricBatalla12",
            comments="Fondos de pantalla animados hechos a medida para Noctalia + Hyprland.",
            website="https://github.com/EricBatalla12/NoHland",
        ).present(self.ventana)

    def _arrancar_daemon(self, *_):
        self.reiniciar_daemon(arrancar=True)

    def reiniciar_daemon(self, arrancar: bool = False) -> None:
        # «Arrancar» tambien lo habilita: si solo se arrancara, tras reiniciar
        # el equipo NoHland no volveria a arrancar solo
        orden = ["enable", "--now"] if arrancar else ["restart"]
        verbo = " ".join(orden)

        def al_terminar(r, error):
            if error or r.returncode:
                detalle = error or r.stderr.strip()
                self.ventana.avisar(f"systemctl --user {verbo} nohland falló: {detalle}")
            else:
                self.ventana.avisar("NoHland arrancado; desde ahora arranca solo al iniciar sesión" if arrancar
                                    else "Ajustes aplicados; NoHland reiniciado")
            self.ventana._refrescar()
        en_hilo(lambda: subprocess.run(["systemctl", "--user", *orden, "nohland"],
                                       capture_output=True, text=True, timeout=30), al_terminar)


def main() -> int:
    return Aplicacion().run([])
