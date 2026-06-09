import os
import io
import re
import ssl
import time
import base64
import json
import subprocess
import threading
import urllib.request
import urllib.parse
import digitalio
import board
from PIL import Image, ImageDraw, ImageFont
import adafruit_rgb_display.ili9341 as ili9341
import RPi.GPIO as GPIO

# ── Dependencias opcionales ───────────────────────────────────────────────────
try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None
    print("⚠️  paho-mqtt no instalado: pip install paho-mqtt")

try:
    import vlc
except ImportError:
    vlc = None
    print("⚠️  python-vlc no instalado: pip install python-vlc")

try:
    from mutagen import File as MutagenFile
except ImportError:
    MutagenFile = None
    print("⚠️  mutagen no instalado: pip install mutagen")

import xpt2046_circuitpython

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# ==========================================
# 1. CONFIGURACIÓN GENERAL
# ==========================================
PIPE_PATH = "/tmp/shairport-sync-metadata"
COVER_DIR = "/tmp/shairport-sync/.cache/coverart"
SAMPLE_RATE = 44100.0

ANCHO_PANTALLA = 240
ALTO_PANTALLA = 320
COVER_SIZE = 240
INFO_Y_START = COVER_SIZE
MARGEN = 10

COLOR_FONDO = (0, 0, 0)
COLOR_FONDO_BARRA = (60, 60, 60)
COLOR_PROGRESO = (255, 255, 255)

# Transición slide
SLIDE_DURACION = 0.5

# Volumen overlay
VOL_VISIBLE_SEG = 1.5
VOL_FADEOUT_SEG = 0.5
VOL_TOTAL_SEG = VOL_VISIBLE_SEG + VOL_FADEOUT_SEG

# Touch
TOUCH_DEBOUNCE = 0.8

# Letras
LRCLIB_URL = "https://lrclib.net/api/get"

# ── Spotify Web API (opcional) ────────────────────────────────────────────────
SPOTIFY_CLIENT_ID     = "04a73804dadc4d5493108182969c59cb"
SPOTIFY_CLIENT_SECRET = "a864785fdc724dcc8cb6f622f5b7cbe6"

SPOTIFY_EVENTS_DIR    = "/tmp/spotify-events"
_spotify_token        = ""
_spotify_token_expiry = 0.0

# ── MQTT (Mosquitto) ──────────────────────────────────────────────────────────
MQTT_BROKER        = "localhost"
MQTT_PORT          = 1883
MQTT_USER          = ""
MQTT_PASSWORD      = ""
MQTT_TOPIC_META    = "reproductor/metadata"
MQTT_TOPIC_STATUS  = "reproductor/status"
MQTT_TOPIC_COVER   = "reproductor/cover"
MQTT_TOPIC_PROGRESO= "reproductor/progreso"
MQTT_TOPIC_CMD     = "reproductor/cmd"
MQTT_TOPIC_FILE    = "reproductor/archivo"
MQTT_TOPIC_VOLUMEN = "reproductor/volumen"

# ── Reproductor local ─────────────────────────────────────────────────────────
EXTENSIONES_AUDIO  = {".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".opus", ".wma"}

# Fuentes
try:
    fuente_titulo = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    fuente_artista = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    fuente_album = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    fuente_tiempo = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    fuente_volumen = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
    fuente_letra_info = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    fuente_letra_activa = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    fuente_letra_inactiva = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
except IOError:
    _def = ImageFont.load_default()
    fuente_titulo = _def
    fuente_artista = _def
    fuente_album = _def
    fuente_tiempo = _def
    fuente_volumen = _def
    fuente_letra_info = _def
    fuente_letra_activa = _def
    fuente_letra_inactiva = _def


# ==========================================
# 2. FUNCIONES DE COLOR
# ==========================================

def extraer_color_dominante(imagen):
    try:
        pequena = imagen.resize((1, 1), Image.LANCZOS)
        r, g, b = pequena.getpixel((0, 0))
        factor = 0.35
        return (int(r * factor), int(g * factor), int(b * factor))
    except Exception:
        return (20, 20, 20)


def luminancia(color):
    r, g, b = color
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def mezclar_color(c1, c2, t):
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def generar_colores(color_fondo):
    """Genera paleta adaptativa para toda la UI."""
    lum = luminancia(color_fondo)
    fr, fg, fb = color_fondo

    if lum > 0.45:
        return {
            "titulo":       (0, 0, 0),
            "artista":      (40, 40, 40),
            "album":        (70, 70, 70),
            "letra_activa": (0, 0, 0),
            "letra_previa": mezclar_color((fr, fg, fb), (40, 40, 40), 0.6),
            "letra_siguiente": mezclar_color((fr, fg, fb), (60, 60, 60), 0.5),
            "info":         mezclar_color((fr, fg, fb), (50, 50, 50), 0.5),
            "separador":    mezclar_color((fr, fg, fb), (0, 0, 0), 0.3),
            "barra_bg":     mezclar_color((fr, fg, fb), (0, 0, 0), 0.25),
            "barra_fg":     (0, 0, 0),
            "tiempo":       (50, 50, 50),
            "msg":          (80, 80, 80),
        }
    else:
        return {
            "titulo":       (255, 255, 255),
            "artista":      (200, 200, 200),
            "album":        (150, 150, 150),
            "letra_activa": (255, 255, 255),
            "letra_previa": mezclar_color((fr, fg, fb), (120, 120, 120), 0.5),
            "letra_siguiente": mezclar_color((fr, fg, fb), (200, 200, 200), 0.45),
            "info":         mezclar_color((fr, fg, fb), (200, 200, 200), 0.4),
            "separador":    mezclar_color((fr, fg, fb), (255, 255, 255), 0.15),
            "barra_bg":     mezclar_color((fr, fg, fb), (255, 255, 255), 0.2),
            "barra_fg":     (255, 255, 255),
            "tiempo":       mezclar_color((fr, fg, fb), (220, 220, 220), 0.4),
            "msg":          mezclar_color((fr, fg, fb), (180, 180, 180), 0.5),
        }


# ==========================================
# 3. ESTADO DEL REPRODUCTOR (thread-safe)
# ==========================================

class EstadoReproductor:
    def __init__(self):
        self.lock = threading.Lock()
        self.titulo = ""
        self.artista = ""
        self.album = ""
        self.duracion_seg = None
        self.posicion_seg = None
        self.timestamp_posicion = None
        self.esta_pausado = False
        self.volumen_pct = 50.0
        self.timestamp_volumen = 0
        self.letras_sync = []
        self.letras_estado = "idle"
        self.letras_mensaje = ""
        self.hubo_cambio_cancion = False
        # Fuente activa: "airplay" | "spotify" | "local"
        self.fuente = ""
        # Carátulas
        self.cover_spotify = None
        self.cover_spotify_cambio = False
        self.cover_local = None
        self.cover_local_cambio = False
        # Comandos pendientes desde MQTT
        self.archivo_pendiente = None
        self.volumen_pendiente = None

    def obtener_posicion_actual(self):
        with self.lock:
            if self.posicion_seg is None or self.timestamp_posicion is None:
                return 0.0
            if self.esta_pausado:
                return self.posicion_seg
            transcurrido = time.time() - self.timestamp_posicion
            pos = self.posicion_seg + transcurrido
            if self.duracion_seg and pos > self.duracion_seg:
                return self.duracion_seg
            return max(0.0, pos)

    def obtener_duracion(self):
        with self.lock:
            return self.duracion_seg

    def obtener_metadata(self):
        with self.lock:
            cambio = self.hubo_cambio_cancion
            self.hubo_cambio_cancion = False
            return self.titulo, self.artista, self.album, cambio

    def obtener_volumen(self):
        with self.lock:
            return self.volumen_pct, self.timestamp_volumen

    def obtener_letras(self):
        with self.lock:
            return list(self.letras_sync), self.letras_estado, self.letras_mensaje

    def obtener_cover_spotify(self):
        with self.lock:
            cambio = self.cover_spotify_cambio
            self.cover_spotify_cambio = False
            return self.cover_spotify, cambio

    def obtener_cover_local(self):
        with self.lock:
            cambio = self.cover_local_cambio
            self.cover_local_cambio = False
            return self.cover_local, cambio

    def snapshot_mqtt(self):
        with self.lock:
            return {
                "titulo":   self.titulo,
                "artista":  self.artista,
                "album":    self.album,
                "fuente":   self.fuente,
                "pausado":  self.esta_pausado,
                "volumen":  int(self.volumen_pct),
                "duracion": round(self.duracion_seg, 1) if self.duracion_seg else None,
            }


# ==========================================
# 4. LECTOR DEL PIPE DE METADATA
# ==========================================

def hex_a_ascii(hex_str):
    try:
        return bytes.fromhex(hex_str.strip()).decode("ascii", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def procesar_item(estado, xml_str):
    try:
        match_type = re.search(r"<type>([0-9a-fA-F]+)</type>", xml_str)
        match_code = re.search(r"<code>([0-9a-fA-F]+)</code>", xml_str)
        match_data = re.search(r"<data[^>]*>(.*?)</data>", xml_str, re.DOTALL)

        if not match_type or not match_code:
            return

        tipo = hex_a_ascii(match_type.group(1))
        code = hex_a_ascii(match_code.group(1))
        data = ""

        if match_data:
            b64 = match_data.group(1).strip()
            if b64:
                try:
                    data = base64.b64decode(b64).decode("utf-8", errors="replace")
                except Exception:
                    data = ""

        ahora = time.time()

        with estado.lock:
            if tipo == "core":
                if code == "minm":
                    if data != estado.titulo:
                        estado.titulo = data
                        estado.hubo_cambio_cancion = True
                        estado.fuente = "airplay" 
                elif code == "asar":
                    estado.artista = data
                elif code == "asal":
                    estado.album = data

            elif tipo == "ssnc":
                if code == "prgr":
                    partes = data.split("/")
                    if len(partes) == 3:
                        try:
                            rtp_start = int(partes[0])
                            rtp_current = int(partes[1])
                            rtp_end = int(partes[2])
                            estado.duracion_seg = max(0.0, (rtp_end - rtp_start) / SAMPLE_RATE)
                            estado.posicion_seg = max(0.0, (rtp_current - rtp_start) / SAMPLE_RATE)
                            estado.timestamp_posicion = ahora
                        except ValueError:
                            pass

                elif code == "pfls" or code == "pend":
                    if not estado.esta_pausado and estado.posicion_seg is not None and estado.timestamp_posicion is not None:
                        estado.posicion_seg += (ahora - estado.timestamp_posicion)
                    estado.esta_pausado = True
                    estado.timestamp_posicion = ahora

                elif code == "prsm" or code == "pbeg":
                    estado.esta_pausado = False
                    estado.timestamp_posicion = ahora

                elif code == "pvol":
                    try:
                        airplay_vol = float(data.split(",")[0])
                        if airplay_vol <= -144.0:
                            estado.volumen_pct = 0.0
                        else:
                            estado.volumen_pct = max(0.0, min(100.0, ((airplay_vol + 30.0) / 30.0) * 100.0))
                        estado.timestamp_volumen = ahora
                    except (ValueError, IndexError):
                        pass
    except Exception:
        pass


def hilo_lector_pipe(estado):
    print("📡 Conectando al pipe de metadata...")
    while True:
        try:
            with open(PIPE_PATH, "r") as pipe:
                print("📡 Pipe conectado.")
                buffer = ""
                for linea in pipe:
                    buffer += linea
                    while "</item>" in buffer:
                        idx = buffer.index("</item>") + len("</item>")
                        procesar_item(estado, buffer[:idx])
                        buffer = buffer[idx:]
        except Exception as e:
            print(f"⚠️  Error en pipe: {e}. Reconectando en 2s...")
            time.sleep(2)


# ==========================================
# 5. LETRAS SINCRONIZADAS (LRCLIB)
# ==========================================

def parsear_lrc(lrc_text):
    lineas = []
    for linea in lrc_text.strip().split("\n"):
        match = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)", linea)
        if match:
            mins = int(match.group(1))
            secs = float(match.group(2))
            texto = match.group(3).strip()
            if texto:
                lineas.append((mins * 60 + secs, texto))
    lineas.sort(key=lambda x: x[0])
    return lineas


def buscar_letras(estado, titulo, artista):
    with estado.lock:
        estado.letras_estado = "cargando"
        estado.letras_sync = []
        estado.letras_mensaje = "Buscando letras..."
        duracion_actual = estado.duracion_seg

    print(f"🔍 Buscando letras: {titulo} - {artista}")
    time.sleep(0.5)

    try:
        # Limpiar metadatos para mejor búsqueda
        t_limpio = re.sub(r'\s*[\(\[].*?[\)\]]\s*', '', titulo).strip()
        a_limpio = re.sub(r'\s*[\(\[].*?[\)\]]\s*', '', artista).strip()

        query = urllib.parse.quote(f"{a_limpio} {t_limpio}")
        url = f"https://lrclib.net/api/search?q={query}"
        req = urllib.request.Request(url, headers={"User-Agent": "RaspberryMusicPlayer/2.0"})

        with urllib.request.urlopen(req, timeout=5) as resp:
            resultados = json.loads(resp.read().decode())

        if resultados and isinstance(resultados, list) and len(resultados) > 0:
            mejor = resultados[0]

            if mejor.get("syncedLyrics"):
                parsed = parsear_lrc(mejor["syncedLyrics"])
                if parsed:
                    with estado.lock:
                        estado.letras_sync = parsed
                        estado.letras_estado = "encontradas"
                        estado.letras_mensaje = ""
                    print(f"✅ Letras sincronizadas ({len(parsed)} líneas)")
                    return

            if mejor.get("plainLyrics"):
                lineas_texto = [l.strip() for l in mejor["plainLyrics"].split("\n") if l.strip()]
                if lineas_texto:
                    dur = duracion_actual if duracion_actual and duracion_actual > 0 else 180.0
                    tiempo_por_linea = dur / (len(lineas_texto) + 1)
                    lineas = [(i * tiempo_por_linea, txt) for i, txt in enumerate(lineas_texto)]
                    with estado.lock:
                        estado.letras_sync = lineas
                        estado.letras_estado = "solo_texto"
                        estado.letras_mensaje = "Auto-Scroll"
                    print(f"📝 Letras sin sync ({len(lineas)} líneas)")
                    return

            with estado.lock:
                estado.letras_estado = "no_encontradas"
                estado.letras_mensaje = "Instrumental / Sin letra"
        else:
            with estado.lock:
                estado.letras_estado = "no_encontradas"
                estado.letras_mensaje = "Letra no encontrada"

        print(f"❌ {estado.letras_mensaje}")

    except Exception as e:
        print(f"❌ Error buscando letras: {e}")
        with estado.lock:
            estado.letras_estado = "no_encontradas"
            estado.letras_mensaje = "Sin conexión a internet"


def iniciar_busqueda_letras(estado, titulo, artista):
    hilo = threading.Thread(target=buscar_letras, args=(estado, titulo, artista), daemon=True)
    hilo.start()


# ==========================================
# 5b. SPOTIFY CONNECT (librespot)
# ==========================================

SPOTIFY_EVENTS_DIR   = "/tmp/spotify-events"
_spotify_token        = ""
_spotify_token_expiry = 0.0


def _obtener_token_spotify():
    global _spotify_token, _spotify_token_expiry
    if _spotify_token and time.time() < _spotify_token_expiry:
        return _spotify_token
        
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        print("⚠️  [Spotify API] Faltan credenciales en el script. Solo obtendrás título y carátula.")
        return ""
        
    try:
        creds = base64.b64encode(
            f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
        ).decode()
        
        # URL oficial corregida
        req = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {creds}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
            data = json.loads(resp.read().decode())
            
        _spotify_token        = data["access_token"]
        _spotify_token_expiry = time.time() + data.get("expires_in", 3600) - 60
        print("🔑 [Spotify API] Token Autenticado OK")
        return _spotify_token
    except Exception as e:
        print(f"⚠️  [Spotify API] Error al obtener Token: {e}")
        return ""


def obtener_metadata_webapi(track_id):
    """Spotify Web API: título, artistas, álbum, duración, portada."""
    tid   = track_id.split(":")[-1] if ":" in track_id else track_id
    token = _obtener_token_spotify()
    
    if not token:
        return None
        
    try:
        # URL oficial corregida
        req = urllib.request.Request(
            f"https://api.spotify.com/v1/tracks/{tid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=8, context=SSL_CTX) as resp:
            d = json.loads(resp.read().decode())
            
        titulo   = d.get("name", "")
        # Une múltiples artistas si los hay (ej. Daft Punk, Pharrell Williams)
        artistas = ", ".join(a["name"] for a in d.get("artists", []))
        album    = d.get("album", {}).get("name", "")
        dur_ms   = d.get("duration_ms", 0)
        
        # Busca la carátula más grande disponible
        imgs     = d.get("album", {}).get("images", [])
        cover    = imgs[0]["url"] if imgs else ""
        
        print(f"🎵 [Spotify API] Track: {titulo!r} | Artista: {artistas!r} | Álbum: {album!r}")
        
        return {
            "titulo": titulo, 
            "artista": artistas, 
            "album": album,
            "duracion_seg": dur_ms / 1000.0 if dur_ms else None, 
            "cover_url": cover
        }
    except Exception as e:
        print(f"⚠️  [Spotify API] Error extrayendo metadata: {e}")
        return None


def obtener_info_logs():
    """Fallback: título y duración desde journalctl de librespot."""
    titulo = ""
    duracion_seg = None
    try:
        res = subprocess.run(
            ["journalctl", "-u", "spotify-connect", "-n", "20",
             "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=3,
        )
        for linea in reversed(res.stdout.split("\n")):
            m = re.search(r'<(.+?)>\s*\((\d+)\s*ms\)\s*loaded', linea)
            if m:
                titulo = m.group(1)
                duracion_seg = int(m.group(2)) / 1000.0
                break
    except Exception:
        pass
    return titulo, duracion_seg


def descargar_imagen(url):
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "RaspberryMusicPlayer/2.0"})
        with urllib.request.urlopen(req, timeout=8, context=SSL_CTX) as resp:
            raw = resp.read()
        img = Image.open(io.BytesIO(raw))
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img.resize((COVER_SIZE, COVER_SIZE))
    except Exception as e:
        print(f"⚠️  Error imagen: {e}")
        return None


SPOTIFY_EVENTS_DIR = "/tmp/spotify-events"

def leer_archivo_evento(nombre):
    try:
        with open(os.path.join(SPOTIFY_EVENTS_DIR, nombre), "r") as f:
            return f.read().strip()
    except Exception:
        return ""

def hilo_spotify_eventos(estado):
    print("🎵 [Spotify] Monitor de Eventos Nativo iniciado")
    ultimo_track_id  = ""
    ultimo_timestamp = ""
    ultimo_cover_url = ""
    
    while True:
        try:
            ts = leer_archivo_evento("timestamp")
            # Si no hay timestamp nuevo, esperamos medio segundo
            if not ts or ts == ultimo_timestamp:
                time.sleep(0.5)
                continue
                
            ultimo_timestamp = ts
            evento      = leer_archivo_evento("event")
            track_id    = leer_archivo_evento("track_id")
            position_ms = leer_archivo_evento("position_ms")
            
            if not track_id:
                continue
                
            ahora = time.time()
            
            # ¿Cambió la canción? Bajamos los datos y la carátula
            if track_id != ultimo_track_id:
                ultimo_track_id = track_id
                print(f"🎵 [Spotify] Nueva canción: {track_id}")
                
                # Llamamos a tu función de la API Web de Spotify que arreglamos antes
                meta = obtener_metadata_webapi(track_id)
                if meta:
                    with estado.lock:
                        estado.titulo              = meta["titulo"]
                        estado.artista             = meta["artista"]
                        estado.album               = meta["album"]
                        estado.duracion_seg        = meta["duracion_seg"]
                        estado.hubo_cambio_cancion = True
                        estado.fuente              = "spotify"
                        
                    cover_url = meta["cover_url"]
                    if cover_url and cover_url != ultimo_cover_url:
                        ultimo_cover_url = cover_url
                        cover = descargar_imagen(cover_url)
                        if cover:
                            with estado.lock:
                                estado.cover_spotify        = cover
                                estado.cover_spotify_cambio = True

            # Controlamos el estado de Pausa/Play y la barra de tiempo
            with estado.lock:
                if estado.fuente == "spotify":
                    if evento == "playing":
                        pos = int(position_ms) if position_ms else 0
                        estado.posicion_seg       = pos / 1000.0
                        estado.timestamp_posicion = ahora
                        estado.esta_pausado       = False
                    elif evento == "paused" or evento == "stopped":
                        estado.esta_pausado = True
                    elif evento == "changed":
                        estado.esta_pausado = False
                        
        except Exception as e:
            pass
            
        time.sleep(0.5)


# ==========================================
# 5c. REPRODUCTOR LOCAL (VLC + mutagen)
# ==========================================

class ReproductorLocal:
    def __init__(self, estado):
        self.estado      = estado
        self.instancia   = vlc.Instance("--no-video", "--quiet") if vlc else None
        self.player      = self.instancia.media_player_new() if self.instancia else None
        self._playlist   = []
        self._idx_actual = -1
        self._generacion = 0

    def reproducir(self, ruta):
        if not self.player:
            print("⚠️  [Local] VLC no disponible")
            return
        ruta = os.path.expanduser(ruta)
        if not os.path.isfile(ruta):
            print(f"⚠️  [Local] No existe: {ruta}")
            return
        if os.path.splitext(ruta)[1].lower() not in EXTENSIONES_AUDIO:
            print(f"⚠️  [Local] Extensión no soportada")
            return
        self._cargar_playlist(ruta)
        self._reproducir_archivo(ruta)

    def _cargar_playlist(self, ruta_actual):
        carpeta = os.path.dirname(os.path.abspath(ruta_actual))
        try:
            self._playlist = sorted([
                os.path.join(carpeta, f) for f in os.listdir(carpeta)
                if os.path.splitext(f)[1].lower() in EXTENSIONES_AUDIO
            ])
            abs_ruta = os.path.abspath(ruta_actual)
            self._idx_actual = (self._playlist.index(abs_ruta)
                                if abs_ruta in self._playlist else 0)
        except Exception:
            self._playlist   = [ruta_actual]
            self._idx_actual = 0

    def _reproducir_archivo(self, ruta):
        print(f"🎵 [Local] {ruta}")
        titulo, artista, album, duracion, cover_img = self._extraer_metadata(ruta)
        self._generacion += 1
        mi_gen = self._generacion
        with self.estado.lock:
            self.estado.titulo              = titulo
            self.estado.artista             = artista
            self.estado.album               = album
            self.estado.duracion_seg        = duracion
            self.estado.posicion_seg        = 0.0
            self.estado.timestamp_posicion  = time.time()
            self.estado.esta_pausado        = False
            self.estado.hubo_cambio_cancion = True
            self.estado.fuente              = "local"
            if cover_img:
                self.estado.cover_local        = cover_img
                self.estado.cover_local_cambio = True
        media = self.instancia.media_new(ruta)
        self.player.set_media(media)
        self.player.play()
        threading.Thread(target=self._hilo_posicion,
                         args=(mi_gen,), daemon=True).start()
        if titulo:
            iniciar_busqueda_letras(self.estado, titulo, artista)

    def siguiente(self):
        if not self._playlist:
            return
        self._idx_actual = (self._idx_actual + 1) % len(self._playlist)
        self._reproducir_archivo(self._playlist[self._idx_actual])

    def anterior(self):
        if not self._playlist:
            return
        self._idx_actual = (self._idx_actual - 1) % len(self._playlist)
        self._reproducir_archivo(self._playlist[self._idx_actual])

    def pausar_reanudar(self):
        if not self.player:
            return
        self.player.pause()
        with self.estado.lock:
            if self.estado.fuente == "local":
                ahora = time.time()
                if self.estado.esta_pausado:
                    self.estado.esta_pausado       = False
                    self.estado.timestamp_posicion = ahora
                else:
                    if (self.estado.posicion_seg is not None and
                            self.estado.timestamp_posicion is not None):
                        self.estado.posicion_seg += ahora - self.estado.timestamp_posicion
                    self.estado.esta_pausado       = True
                    self.estado.timestamp_posicion = ahora

    def detener(self):
        if self.player:
            self.player.stop()
        with self.estado.lock:
            if self.estado.fuente == "local":
                self.estado.esta_pausado = True

    def set_volumen(self, pct):
        if not self.player:
            return
        pct = max(0, min(100, int(pct)))
        self.player.audio_set_volume(pct)
        with self.estado.lock:
            self.estado.volumen_pct       = float(pct)
            self.estado.timestamp_volumen = time.time()

    def _extraer_metadata(self, ruta):
        titulo    = os.path.splitext(os.path.basename(ruta))[0]
        artista   = ""
        album     = ""
        duracion  = None
        cover_img = None
        if not MutagenFile:
            return titulo, artista, album, duracion, cover_img
        try:
            audio = MutagenFile(ruta, easy=True)
            if audio:
                titulo  = str(audio.get("title",  [titulo])[0])
                artista = str(audio.get("artist", [""])[0])
                album   = str(audio.get("album",  [""])[0])
                if hasattr(audio, "info") and hasattr(audio.info, "length"):
                    duracion = float(audio.info.length)
            audio_raw = MutagenFile(ruta)
            if audio_raw:
                data_bytes = None
                if hasattr(audio_raw, "tags") and audio_raw.tags:
                    for k in audio_raw.tags.keys():
                        if k.startswith("APIC"):
                            data_bytes = audio_raw.tags[k].data
                            break
                if data_bytes is None and getattr(audio_raw, "pictures", None):
                    data_bytes = audio_raw.pictures[0].data
                if data_bytes is None and "covr" in (audio_raw.tags or {}):
                    covers = audio_raw.tags["covr"]
                    if covers:
                        data_bytes = bytes(covers[0])
                if data_bytes:
                    img = Image.open(io.BytesIO(data_bytes))
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    cover_img = img.resize((COVER_SIZE, COVER_SIZE))
        except Exception as e:
            print(f"⚠️  [Local] Mutagen: {e}")
        return titulo, artista, album, duracion, cover_img

    def _hilo_posicion(self, mi_gen):
        while True:
            time.sleep(0.5)
            if mi_gen != self._generacion or not self.player:
                break
            with self.estado.lock:
                if self.estado.fuente != "local" or self.estado.esta_pausado:
                    continue
            try:
                pos_ms = self.player.get_time()
                if pos_ms >= 0:
                    with self.estado.lock:
                        self.estado.posicion_seg       = pos_ms / 1000.0
                        self.estado.timestamp_posicion = time.time()
                state = self.player.get_state()
                if state == vlc.State.Ended:
                    with self.estado.lock:
                        if self.estado.fuente == "local":
                            self.estado.esta_pausado = True
                    if self._playlist and len(self._playlist) > 1:
                        time.sleep(0.5)
                        if mi_gen == self._generacion:
                            self.siguiente()
                    break
                elif state == vlc.State.Error:
                    if mi_gen == self._generacion and len(self._playlist) > 1:
                        time.sleep(0.5)
                        self.siguiente()
                    break
            except Exception:
                break


# ==========================================
# 5d. MIDDLEWARE MQTT
# ==========================================

class MQTTMiddleware:
    def __init__(self, estado, reproductor_local):
        self.estado            = estado
        self.reproductor_local = reproductor_local
        self.client            = None
        self._conectado        = False
        self._ultimo_titulo    = ""
        if mqtt is None:
            print("⚠️  [MQTT] Desactivado (paho-mqtt ausente)")
            return
        self.client = mqtt.Client(client_id="rpi-reproductor", clean_session=True)
        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message    = self._on_message
        threading.Thread(target=self._hilo_conexion, daemon=True).start()
        threading.Thread(target=self._hilo_publicar, daemon=True).start()

    def _on_connect(self, client, ud, fl, rc):
        if rc == 0:
            self._conectado = True
            print(f"🌐 [MQTT] Conectado a {MQTT_BROKER}:{MQTT_PORT}")
            client.subscribe(MQTT_TOPIC_CMD)
            client.subscribe(MQTT_TOPIC_FILE)
            client.subscribe(MQTT_TOPIC_VOLUMEN)

    def _on_disconnect(self, client, ud, rc):
        self._conectado = False

    def _on_message(self, client, ud, msg):
        topic   = msg.topic
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        if topic == MQTT_TOPIC_CMD:
            cmd = payload.lower()
            fuente = self.estado.fuente
            if cmd == "play":
                if fuente == "local":
                    self.reproductor_local.pausar_reanudar()
                else:
                    _dbus_cmd("PlayPause")
            elif cmd == "pause":
                if fuente == "local":
                    self.reproductor_local.pausar_reanudar()
                else:
                    _dbus_cmd("PlayPause")
            elif cmd == "next":
                if fuente == "local":
                    self.reproductor_local.siguiente()
                else:
                    _dbus_cmd("Next")
            elif cmd == "prev":
                if fuente == "local":
                    self.reproductor_local.anterior()
                else:
                    _dbus_cmd("Previous")
            elif cmd == "stop":
                self.reproductor_local.detener()
        elif topic == MQTT_TOPIC_FILE:
            with self.estado.lock:
                self.estado.archivo_pendiente = payload
        elif topic == MQTT_TOPIC_VOLUMEN:
            try:
                with self.estado.lock:
                    self.estado.volumen_pendiente = int(float(payload))
            except ValueError:
                pass

    def _hilo_conexion(self):
        while True:
            if not self._conectado:
                try:
                    self.client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
                    self.client.loop_start()
                except Exception as e:
                    print(f"⚠️  [MQTT] {e}")
            time.sleep(10)

    def _hilo_publicar(self):
        while True:
            time.sleep(1)
            if not self._conectado or not self.client:
                continue
            try:
                snap = self.estado.snapshot_mqtt()
                if snap["titulo"] != self._ultimo_titulo:
                    self._ultimo_titulo = snap["titulo"]
                    self.client.publish(
                        MQTT_TOPIC_META,
                        json.dumps({"titulo": snap["titulo"], "artista": snap["artista"],
                                    "album": snap["album"], "fuente": snap["fuente"]},
                                   ensure_ascii=False),
                        retain=True,
                    )
                status = "paused" if snap["pausado"] else (
                    "stopped" if not snap["titulo"] else "playing")
                self.client.publish(MQTT_TOPIC_STATUS, status, retain=True)
                pos = self.estado.obtener_posicion_actual()
                self.client.publish(
                    MQTT_TOPIC_PROGRESO,
                    json.dumps({"posicion": round(pos, 1),
                                "duracion": snap["duracion"]}),
                )
            except Exception as e:
                print(f"⚠️  [MQTT pub] {e}")


# ==========================================
# 6. BOTONES (Lógica por Interrupciones)
# ==========================================
import os
import RPi.GPIO as GPIO

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Reasignación final: K3 = Pausa/Play, K2 = Siguiente
BTN_K3_PAUSA = 24
BTN_K2_NEXT  = 23

GPIO.setup(BTN_K3_PAUSA, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BTN_K2_NEXT,  GPIO.IN, pull_up_down=GPIO.PUD_UP)

def _dbus_cmd(cmd):
    """Envía comandos a AirPlay/Spotify usando playerctl o dbus."""
    print(f"📡 Comando enviado: {cmd}")
    pct_cmd = "play-pause" if cmd == "PlayPause" else ("next" if cmd == "Next" else "previous")
    
    os.system(f"playerctl {pct_cmd} >/dev/null 2>&1 &")
    
    for dest in ("ShairportSync", "librespot", "raspotify"):
        os.system(
            f"dbus-send --system --type=method_call "
            f"--dest=org.mpris.MediaPlayer2.{dest} "
            f"/org/mpris/MediaPlayer2 "
            f"org.mpris.MediaPlayer2.Player.{cmd} >/dev/null 2>&1 &"
        )

def control_musica(canal):
    """Callback disparado por interrupción de hardware."""
    fuente = estado.fuente if 'estado' in globals() else ""
    print(f"\n👉 [BOTÓN] Interrupción en BCM{canal} | Fuente: '{fuente}'")
    
    if canal == BTN_K3_PAUSA:
        if fuente == "local":
            reproductor_local.pausar_reanudar()
        else:
            _dbus_cmd("PlayPause")
            
    elif canal == BTN_K2_NEXT:
        if fuente == "local":
            reproductor_local.siguiente()
        else:
            _dbus_cmd("Next")

# Asignamos las interrupciones a los pines seguros
GPIO.add_event_detect(BTN_K3_PAUSA, GPIO.FALLING, callback=control_musica, bouncetime=300)
GPIO.add_event_detect(BTN_K2_NEXT, GPIO.FALLING, callback=control_musica, bouncetime=300)

modo_letras = False

# ==========================================
# 7. PANTALLA SPI + TOUCH
# ==========================================
cs_pin = digitalio.DigitalInOut(board.CE0)
dc_pin = digitalio.DigitalInOut(board.D22)
reset_pin = digitalio.DigitalInOut(board.D27)
spi = board.SPI()

disp = ili9341.ILI9341(
    spi, rotation=0, cs=cs_pin, dc=dc_pin, rst=reset_pin, baudrate=40000000,
)

# Touch XPT2046 (se lee DESPUÉS de actualizar pantalla para evitar ruido SPI)
cs_touch = digitalio.DigitalInOut(board.CE1)
irq_touch = digitalio.DigitalInOut(board.D17)
touch = xpt2046_circuitpython.Touch(
    spi, cs=cs_touch, interrupt=irq_touch, force_baudrate=4000000,
)
print("👆 Touch configurado (lápiz táctil)")

touch_previo = False
ultimo_touch = 0.0


# ==========================================
# 8. FUNCIONES DE DIBUJO
# ==========================================

def formato_tiempo(segundos):
    if segundos is None or segundos < 0:
        return "--:--"
    s = int(segundos)
    return f"{s // 60}:{s % 60:02d}"


def medir_texto(draw, texto, fuente):
    bbox = draw.textbbox((0, 0), texto, font=fuente)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def medir_texto_multilinea(draw, texto, fuente):
    """Mide el ancho y alto de texto multilínea."""
    lineas = texto.split("\n")
    max_w = 0
    total_h = 0
    for linea in lineas:
        tw, th = medir_texto(draw, linea, fuente)
        if tw > max_w:
            max_w = tw
        total_h += th + 4  # 4px de interlineado
    return max_w, total_h


def envolver_texto(draw, texto, fuente, max_ancho):
    """Corta frases largas en varias líneas para que quepan en la pantalla."""
    lineas = []
    for parrafo in texto.split("\n"):
        palabras = parrafo.split()
        linea_actual = ""
        for palabra in palabras:
            prueba = linea_actual + palabra + " "
            tw, _ = medir_texto(draw, prueba, fuente)
            if tw <= max_ancho:
                linea_actual = prueba
            else:
                if linea_actual:
                    lineas.append(linea_actual.strip())
                linea_actual = palabra + " "
        if linea_actual:
            lineas.append(linea_actual.strip())
    return "\n".join(lineas)


def calcular_offset_scroll(texto_ancho, area_ancho, tiempo_transcurrido):
    exceso = texto_ancho - area_ancho
    if exceso <= 0:
        return 0
    velocidad = 30.0
    pausa = 2.0
    tiempo_scroll = exceso / velocidad
    ciclo = pausa + tiempo_scroll + pausa + tiempo_scroll
    t = tiempo_transcurrido % ciclo
    if t < pausa:
        return 0
    elif t < pausa + tiempo_scroll:
        return int(exceso * ((t - pausa) / tiempo_scroll))
    elif t < pausa + tiempo_scroll + pausa:
        return exceso
    else:
        return int(exceso * (1.0 - (t - pausa - tiempo_scroll - pausa) / tiempo_scroll))


def dibujar_texto_scroll(lienzo, texto, fuente, color, y, tiempo_scroll, color_fondo_scroll=COLOR_FONDO):
    draw = ImageDraw.Draw(lienzo)
    area_ancho = ANCHO_PANTALLA - MARGEN * 2
    texto_ancho, texto_alto = medir_texto(draw, texto, fuente)
    if texto_ancho <= area_ancho:
        x = (ANCHO_PANTALLA - texto_ancho) // 2
        draw.text((x, y), texto, fill=color, font=fuente)
    else:
        offset = calcular_offset_scroll(texto_ancho, area_ancho, tiempo_scroll)
        img_texto = Image.new("RGB", (texto_ancho + 20, texto_alto + 4), color_fondo_scroll)
        draw_t = ImageDraw.Draw(img_texto)
        draw_t.text((0, 0), texto, fill=color, font=fuente)
        ventana = img_texto.crop((offset, 0, offset + area_ancho, texto_alto + 4))
        lienzo.paste(ventana, (MARGEN, y))


def dibujar_barra_progreso(lienzo, posicion_seg, duracion_seg, y_barra, colores):
    draw = ImageDraw.Draw(lienzo)
    barra_h = 4
    barra_x0 = MARGEN
    barra_x1 = ANCHO_PANTALLA - MARGEN

    draw.rectangle([barra_x0, y_barra, barra_x1, y_barra + barra_h], fill=colores["barra_bg"])

    if duracion_seg and duracion_seg > 0 and posicion_seg is not None:
        progreso = max(0.0, min(1.0, posicion_seg / duracion_seg))
    else:
        progreso = 0.0

    largo = int((barra_x1 - barra_x0) * progreso)
    if largo > 0:
        draw.rectangle([barra_x0, y_barra, barra_x0 + largo, y_barra + barra_h], fill=colores["barra_fg"])

    tiempo_y = y_barra + barra_h + 3
    draw.text((barra_x0, tiempo_y), formato_tiempo(posicion_seg), fill=colores["tiempo"], font=fuente_tiempo)
    txt_total = formato_tiempo(duracion_seg)
    ancho_tt, _ = medir_texto(draw, txt_total, fuente_tiempo)
    draw.text((barra_x1 - ancho_tt, tiempo_y), txt_total, fill=colores["tiempo"], font=fuente_tiempo)


# --- MODO CARÁTULA ---

def dibujar_info_cover(lienzo, titulo, artista, album, posicion_seg, duracion_seg,
                       tiempo_scroll, colores, color_fondo_c, fuente_str=""):
    titulo_y = INFO_Y_START + 2
    dibujar_texto_scroll(lienzo, titulo if titulo else "Sin título",
                         fuente_titulo, colores["titulo"], titulo_y, tiempo_scroll, color_fondo_c)

    artista_y = titulo_y + 18
    dibujar_texto_scroll(lienzo, artista if artista else "Artista desconocido",
                         fuente_artista, colores["artista"], artista_y, tiempo_scroll, color_fondo_c)

    album_y = artista_y + 16
    if album:
        dibujar_texto_scroll(lienzo, album, fuente_album, colores["album"], album_y, tiempo_scroll, color_fondo_c)

    barra_y = album_y + 15
    dibujar_barra_progreso(lienzo, posicion_seg, duracion_seg, barra_y, colores)

    # Badge de fuente centrado debajo de la barra
    etiquetas = {"local": "▶ LOCAL", "airplay": "▶ AIRPLAY", "spotify": "▶ SPOTIFY"}
    badge_txt = etiquetas.get(fuente_str.lower(), "")
    if badge_txt:
        draw = ImageDraw.Draw(lienzo)
        tw, _ = medir_texto(draw, badge_txt, fuente_tiempo)
        badge_y = barra_y + 4 + 3 + 14
        draw.text(((ANCHO_PANTALLA - tw) // 2, badge_y),
                  badge_txt, fill=colores["info"], font=fuente_tiempo)


# --- MODO LETRAS (con word wrap) ---

def dibujar_vista_letras(lienzo, titulo, artista, letras_sync, letras_estado,
                         letras_mensaje, posicion_seg, duracion_seg, colores):
    """Dibuja letras con word wrap. Línea actual centrada, las demás fluyen arriba/abajo."""
    draw = ImageDraw.Draw(lienzo)
    max_ancho_texto = ANCHO_PANTALLA - 20  # 10px margen cada lado

    # --- Header compacto ---
    header = titulo if titulo else "Sin título"
    if artista:
        header += f"  •  {artista}"
    # Truncar header si es muy largo
    while len(header) > 3:
        hw, _ = medir_texto(draw, header, fuente_letra_info)
        if hw <= ANCHO_PANTALLA - 20:
            break
        header = header[:-4] + "…"
    hw, _ = medir_texto(draw, header, fuente_letra_info)
    draw.text(((ANCHO_PANTALLA - hw) // 2, 6), header,
              fill=colores["info"], font=fuente_letra_info)

    draw.line([(MARGEN, 22), (ANCHO_PANTALLA - MARGEN, 22)],
              fill=colores["separador"], width=1)

    # --- Zona de letras (y=28 a y=282) ---
    zona_y_inicio = 28
    zona_y_fin = 282

    if not letras_sync:
        # Sin letras: mostrar mensaje
        msg = letras_mensaje if letras_mensaje else "Esperando canción..."
        msg_wrap = envolver_texto(draw, msg, fuente_artista, max_ancho_texto)
        mw, mh = medir_texto_multilinea(draw, msg_wrap, fuente_artista)
        y_msg = zona_y_inicio + (zona_y_fin - zona_y_inicio) // 2 - mh // 2
        draw.multiline_text(
            ((ANCHO_PANTALLA - mw) // 2, y_msg), msg_wrap,
            fill=colores["msg"], font=fuente_artista, align="center",
        )
    else:
        # Encontrar verso actual
        idx_actual = 0
        for i, (t, _) in enumerate(letras_sync):
            if (posicion_seg or 0) >= t:
                idx_actual = i
            else:
                break

        y_centro = zona_y_inicio + (zona_y_fin - zona_y_inicio) // 2
        espaciado = 12

        # 1. LÍNEA ACTUAL (centrada, blanca/bold)
        if idx_actual < len(letras_sync):
            txt_wrap = envolver_texto(draw, letras_sync[idx_actual][1],
                                     fuente_letra_activa, max_ancho_texto)
            tw, th = medir_texto_multilinea(draw, txt_wrap, fuente_letra_activa)
            y_dibujo = y_centro - th // 2
            draw.multiline_text(
                ((ANCHO_PANTALLA - tw) // 2, y_dibujo), txt_wrap,
                fill=colores["letra_activa"], font=fuente_letra_activa, align="center",
            )
            y_arriba = y_dibujo - espaciado
            y_abajo = y_dibujo + th + espaciado
        else:
            y_arriba = y_centro - espaciado
            y_abajo = y_centro + espaciado

        # 2. LÍNEAS ANTERIORES (fluyen hacia arriba, gris oscuro)
        for i in range(idx_actual - 1, max(-1, idx_actual - 5), -1):
            txt_wrap = envolver_texto(draw, letras_sync[i][1],
                                     fuente_letra_inactiva, max_ancho_texto)
            tw, th = medir_texto_multilinea(draw, txt_wrap, fuente_letra_inactiva)
            y_dibujo = y_arriba - th
            if y_dibujo < zona_y_inicio - 5:
                break
            draw.multiline_text(
                ((ANCHO_PANTALLA - tw) // 2, y_dibujo), txt_wrap,
                fill=colores["letra_previa"], font=fuente_letra_inactiva, align="center",
            )
            y_arriba = y_dibujo - espaciado

        # 3. LÍNEAS SIGUIENTES (fluyen hacia abajo, gris claro)
        for i in range(idx_actual + 1, min(len(letras_sync), idx_actual + 5)):
            txt_wrap = envolver_texto(draw, letras_sync[i][1],
                                     fuente_letra_inactiva, max_ancho_texto)
            tw, th = medir_texto_multilinea(draw, txt_wrap, fuente_letra_inactiva)
            if y_abajo + th > zona_y_fin + 5:
                break
            draw.multiline_text(
                ((ANCHO_PANTALLA - tw) // 2, y_abajo), txt_wrap,
                fill=colores["letra_siguiente"], font=fuente_letra_inactiva, align="center",
            )
            y_abajo += th + espaciado

    # Separador inferior
    draw.line([(MARGEN, zona_y_fin), (ANCHO_PANTALLA - MARGEN, zona_y_fin)],
              fill=colores["separador"], width=1)

    # Barra de progreso
    dibujar_barra_progreso(lienzo, posicion_seg, duracion_seg, zona_y_fin + 6, colores)


# --- VOLUMEN OVERLAY ---

def dibujar_volumen(lienzo, volumen_pct, tiempo_desde_cambio):
    if tiempo_desde_cambio > VOL_TOTAL_SEG:
        return

    if tiempo_desde_cambio <= VOL_VISIBLE_SEG:
        opacidad = 1.0
    else:
        opacidad = 1.0 - (tiempo_desde_cambio - VOL_VISIBLE_SEG) / VOL_FADEOUT_SEG

    ov_ancho = 180
    ov_alto = 36
    ov_x = (ANCHO_PANTALLA - ov_ancho) // 2
    ov_y = 100

    overlay = Image.new("RGB", (ov_ancho, ov_alto), (30, 30, 30))
    draw_ov = ImageDraw.Draw(overlay)

    vol_texto = f"Vol  {int(volumen_pct)}%"
    tw, _ = medir_texto(draw_ov, vol_texto, fuente_volumen)
    draw_ov.text(((ov_ancho - tw) // 2, 2), vol_texto, fill=(255, 255, 255), font=fuente_volumen)

    barra_m = 12
    barra_y = 22
    barra_h = 6
    barra_x1 = ov_ancho - barra_m
    draw_ov.rectangle([barra_m, barra_y, barra_x1, barra_y + barra_h], fill=(80, 80, 80))
    fill_w = int((barra_x1 - barra_m) * volumen_pct / 100.0)
    if fill_w > 0:
        draw_ov.rectangle([barra_m, barra_y, barra_m + fill_w, barra_y + barra_h], fill=(255, 255, 255))

    region = lienzo.crop((ov_x, ov_y, ov_x + ov_ancho, ov_y + ov_alto))
    mezclado = Image.blend(region, overlay, opacidad * 0.85)
    lienzo.paste(mezclado, (ov_x, ov_y))


# ==========================================
# 9. BUCLE PRINCIPAL
# ==========================================

estado             = EstadoReproductor()
reproductor_local  = ReproductorLocal(estado)
mqtt_middleware    = MQTTMiddleware(estado, reproductor_local)

threading.Thread(target=hilo_lector_pipe,     args=(estado,), daemon=True).start()
threading.Thread(target=hilo_spotify_eventos, args=(estado,), daemon=True).start()

# Estado pantalla
last_modified = 0
imagen_caratula = None
imagen_caratula_nueva = None
imagen_caratula_vieja = None
slide_inicio = None

tiempo_inicio_scroll = time.time()
titulo_mostrado = ""
artista_mostrado = ""
album_mostrado = ""
ultimo_track_letras = ""

# Color dominante
color_fondo_dom = (20, 20, 20)
colores = generar_colores(color_fondo_dom)

print("🚀 Sistema Listo. Esperando música desde AirPlay...")
print("👆 Toca la pantalla con el lápiz para alternar entre carátula y letras")

try:
    while True:
        ahora = time.time()

        # ── Comandos pendientes de MQTT ──────────────────────────────────────
        with estado.lock:
            archivo_pendiente = estado.archivo_pendiente
            volumen_pendiente = estado.volumen_pendiente
            estado.archivo_pendiente = None
            estado.volumen_pendiente = None
        if archivo_pendiente:
            reproductor_local.reproducir(archivo_pendiente)
        if volumen_pendiente is not None:
            reproductor_local.set_volumen(volumen_pendiente)

        # --- Verificar nueva carátula ---
        caratula_cambio = False
        if os.path.exists(COVER_DIR):
            archivos = [
                os.path.join(COVER_DIR, f)
                for f in os.listdir(COVER_DIR)
                if os.path.isfile(os.path.join(COVER_DIR, f))
            ]
            if archivos:
                archivo_mas_reciente = max(archivos, key=os.path.getmtime)
                tiempo_modificacion = os.path.getmtime(archivo_mas_reciente)

                if tiempo_modificacion > last_modified:
                    try:
                        image = Image.open(archivo_mas_reciente)
                        if image.mode != "RGB":
                            image = image.convert("RGB")
                        nueva_cover = image.resize((COVER_SIZE, COVER_SIZE))
                        last_modified = tiempo_modificacion
                        caratula_cambio = True

                        # Color dominante
                        color_fondo_dom = extraer_color_dominante(nueva_cover)
                        colores = generar_colores(color_fondo_dom)

                        if imagen_caratula is not None:
                            imagen_caratula_vieja = imagen_caratula.copy()
                            imagen_caratula_nueva = nueva_cover
                            slide_inicio = ahora
                        else:
                            imagen_caratula = nueva_cover

                    except Exception as e:
                        print(f"Error al abrir carátula: {e}")

                    for f in archivos:
                        if f != archivo_mas_reciente:
                            try:
                                os.remove(f)
                            except OSError:
                                pass

        # ── Carátula Spotify ──────────────────────────────────────────────────
        cover_spotify, cover_spotify_cambio = estado.obtener_cover_spotify()
        if cover_spotify_cambio and cover_spotify is not None:
            nueva_cover = cover_spotify
            caratula_cambio = True
            color_fondo_dom = extraer_color_dominante(nueva_cover)
            colores = generar_colores(color_fondo_dom)
            if imagen_caratula is not None:
                imagen_caratula_vieja = imagen_caratula.copy()
                imagen_caratula_nueva = nueva_cover
                slide_inicio = ahora
            else:
                imagen_caratula = nueva_cover

        # ── Carátula Local ────────────────────────────────────────────────────
        cover_local, cover_local_cambio = estado.obtener_cover_local()
        if cover_local_cambio and cover_local is not None:
            nueva_cover = cover_local
            caratula_cambio = True
            color_fondo_dom = extraer_color_dominante(nueva_cover)
            colores = generar_colores(color_fondo_dom)
            if imagen_caratula is not None:
                imagen_caratula_vieja = imagen_caratula.copy()
                imagen_caratula_nueva = nueva_cover
                slide_inicio = ahora
            else:
                imagen_caratula = nueva_cover

        # --- Slide ---
        hay_slide = False
        if slide_inicio is not None:
            t_slide = (ahora - slide_inicio) / SLIDE_DURACION
            if t_slide >= 1.0:
                imagen_caratula = imagen_caratula_nueva
                imagen_caratula_vieja = None
                imagen_caratula_nueva = None
                slide_inicio = None
            else:
                hay_slide = True

        # --- Metadata ---
        titulo, artista, album, cambio_cancion = estado.obtener_metadata()

        if cambio_cancion or caratula_cambio:
            tiempo_inicio_scroll = ahora
            if titulo:
                titulo_mostrado = titulo
            if artista:
                artista_mostrado = artista
            if album:
                album_mostrado = album
            print(f"🎵 {titulo_mostrado} - {artista_mostrado} ({album_mostrado})")

            track_key = f"{titulo_mostrado}|{artista_mostrado}"
            if track_key != ultimo_track_letras and titulo_mostrado:
                ultimo_track_letras = track_key
                iniciar_busqueda_letras(estado, titulo_mostrado, artista_mostrado)
        else:
            if titulo and titulo != titulo_mostrado:
                titulo_mostrado = titulo
                tiempo_inicio_scroll = ahora
            if artista and artista != artista_mostrado:
                artista_mostrado = artista
            if album and album != album_mostrado:
                album_mostrado = album

        # --- Posición y duración ---
        posicion_seg = estado.obtener_posicion_actual()
        duracion_seg = estado.obtener_duracion()

        # --- Volumen ---
        volumen_pct, ts_vol = estado.obtener_volumen()
        tiempo_desde_vol = ahora - ts_vol

        # --- Construir frame ---
        if imagen_caratula is not None or modo_letras:
            lienzo = Image.new("RGB", (ANCHO_PANTALLA, ALTO_PANTALLA), color_fondo_dom)

            if modo_letras:
                # === MODO LETRAS ===
                letras_sync, letras_estado, letras_mensaje = estado.obtener_letras()
                dibujar_vista_letras(
                    lienzo,
                    titulo_mostrado, artista_mostrado,
                    letras_sync, letras_estado, letras_mensaje,
                    posicion_seg, duracion_seg, colores,
                )
            else:
                # === MODO CARÁTULA ===
                if hay_slide:
                    t_slide = (ahora - slide_inicio) / SLIDE_DURACION
                    t_ease = 1.0 - (1.0 - t_slide) ** 2
                    offset = int(COVER_SIZE * t_ease)

                    if imagen_caratula_vieja is not None:
                        vieja_x = -offset
                        if vieja_x > -COVER_SIZE:
                            lienzo.paste(imagen_caratula_vieja, (vieja_x, 0))

                    if imagen_caratula_nueva is not None:
                        nueva_x = COVER_SIZE - offset
                        if nueva_x < COVER_SIZE:
                            lienzo.paste(imagen_caratula_nueva, (nueva_x, 0))
                else:
                    if imagen_caratula is not None:
                        lienzo.paste(imagen_caratula, (0, 0))

                tiempo_scroll = ahora - tiempo_inicio_scroll
                with estado.lock:
                    fuente_actual = estado.fuente
                dibujar_info_cover(
                    lienzo,
                    titulo_mostrado, artista_mostrado, album_mostrado,
                    posicion_seg, duracion_seg,
                    tiempo_scroll, colores, color_fondo_dom, fuente_actual,
                )

            # Volumen overlay
            if tiempo_desde_vol <= VOL_TOTAL_SEG:
                dibujar_volumen(lienzo, volumen_pct, tiempo_desde_vol)

            disp.image(lienzo.convert("RGB"))

            # --- Touch: leer DESPUÉS de pantalla (SPI libre) ---
            try:
                tocado = touch.is_pressed()
                if tocado and not touch_previo:
                    try:
                        x_t, y_t = touch.get_coordinates()
                        if ahora - ultimo_touch > TOUCH_DEBOUNCE:
                            modo_letras = not modo_letras
                            ultimo_touch = ahora
                            print(f"👆 Modo: {'Letras' if modo_letras else 'Carátula'}")
                    except Exception:
                        pass
                touch_previo = tocado
            except Exception:
                touch_previo = False

        time.sleep(0.25)

except KeyboardInterrupt:
    print("\nApagando sistema y liberando pines...")
    if reproductor_local.player:
        reproductor_local.player.stop()
    if mqtt_middleware.client:
        mqtt_middleware.client.loop_stop()
        mqtt_middleware.client.disconnect()
    GPIO.cleanup()
