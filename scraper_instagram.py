"""
Scraping de una cuenta pública de Instagram con Playwright.
Obtiene: datos del perfil (usuario, descripción, foto, seguidores, seguidos, nº de
publicaciones) y N publicaciones con su descripción e imágenes (en tamaño completo,
incluidas todas las de un carrusel).

Uso:
    python scraper_instagram.py nasa
    python scraper_instagram.py nasa --cantidad 30
    python scraper_instagram.py nasa --listas --max-lista 50
    python scraper_instagram.py            (te pregunta el usuario)

--listas guarda los nombres de usuario de seguidores y seguidos de cualquier perfil que tu
sesión pueda ver (público, o privado que sigues). Hace falta haber iniciado sesión.

Los datos de cada perfil quedan ordenados en su propia carpeta:
    datos/<usuario>/perfil.json
    datos/<usuario>/publicaciones.json
    datos/<usuario>/enlaces.json
    datos/<usuario>/foto_perfil.jpg
    datos/<usuario>/imagenes/post_1_1.jpg ...     (post_<n>_<imagen del carrusel>)
    datos/<usuario>/seguidores.json, seguidos.json (solo con --listas)

Autenticación por cookies: se inicia sesión a mano una vez, las cookies se guardan
en cookies_sesion.json (storage_state) y se reutilizan en las siguientes ejecuciones.
Uso académico. Ejecutar pocas veces: los enlaces se guardan en caché.
"""
import argparse
import json
import os
import random
import re
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

CANTIDAD_POR_DEFECTO = 25
MAX_LISTA_POR_DEFECTO = 100
MAX_IMAGENES_POR_POST = 10
CARPETA_DATOS = "datos"
ARCHIVO_COOKIES = "cookies_sesion.json"   # equivale a una contraseña: NO subir a Git

OPCIONES_CONTEXTO = {
    "viewport": {"width": 1366, "height": 768},
    "locale": "es-ES",
}

# Reconoce líneas como "4.000 publicaciones", "98 M seguidores", "100 following"
PATRON_STAT = re.compile(
    r"^([\d.,]+\s?(?:mil|[KkMm])?)\s+(publicaciones|posts|seguidores|followers|seguidos|following)\b",
    re.IGNORECASE,
)
CLAVES_STAT = {
    "publicaciones": "publicaciones", "posts": "publicaciones",
    "seguidores": "seguidores", "followers": "seguidores",
    "seguidos": "seguidos", "following": "seguidos",
}
TEXTOS_BOTONES = {"seguir", "siguiendo", "mensaje", "follow", "following", "message", "contactar", "más", "more"}
RUTAS_RESERVADAS = {"explore", "accounts", "direct", "reels", "reel", "p", "stories", "about", "legal"}

# Devuelve la imagen más visible de la publicación (la del carrusel que se está mostrando),
# usando el candidato de mayor resolución de su srcset. Ignora fotos de perfil.
JS_MEJOR_IMAGEN = r"""() => {
  const raiz = document.querySelector('article') || document.querySelector('main') || document;
  let mejor = null, mejorArea = 0;
  for (const img of raiz.querySelectorAll('img')) {
    const alt = (img.alt || '').toLowerCase();
    if (/profile picture|foto del perfil|imagen del perfil/.test(alt)) continue;
    if (img.naturalWidth < 300) continue;
    const r = img.getBoundingClientRect();
    const w = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
    const h = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
    if (w * h > mejorArea) { mejorArea = w * h; mejor = img; }
  }
  if (!mejor) return null;
  let url = mejor.currentSrc || mejor.src;
  if (mejor.srcset) {
    let anchoMax = 0;
    for (const cand of mejor.srcset.split(/,\s+(?=https?:)/)) {
      const [u, d] = cand.trim().split(/\s+/);
      const w = parseInt(d) || 0;
      if (u && w > anchoMax) { anchoMax = w; url = u; }
    }
  }
  return { url: url, ancho: mejor.naturalWidth, alto: mejor.naturalHeight };
}"""

# Desplaza el elemento con scroll dentro del cuadro de diálogo (lista de seguidores/seguidos)
JS_SCROLL_DIALOGO = r"""d => {
  const el = [...d.querySelectorAll('*')].find(
    e => e.scrollHeight > e.clientHeight + 5 && getComputedStyle(e).overflowY !== 'visible');
  if (el) el.scrollTop = el.scrollHeight;
}"""


def pausa(minimo=3.0, maximo=7.5):
    time.sleep(random.uniform(minimo, maximo))


def normalizar_usuario(texto):
    """Quita @ y espacios; valida que sea un nombre de usuario de Instagram."""
    usuario = texto.strip().lstrip("@").lower()
    if not re.fullmatch(r"[a-z0-9._]{1,30}", usuario):
        raise ValueError(f"'{texto}' no parece un nombre de usuario válido de Instagram.")
    return usuario


def preparar_rutas(usuario):
    """Crea la carpeta del perfil y devuelve las rutas de todos sus archivos."""
    base = os.path.join(CARPETA_DATOS, usuario)
    imagenes = os.path.join(base, "imagenes")
    os.makedirs(imagenes, exist_ok=True)
    return {
        "base": base,
        "imagenes": imagenes,
        "enlaces": os.path.join(base, "enlaces.json"),
        "publicaciones": os.path.join(base, "publicaciones.json"),
        "perfil": os.path.join(base, "perfil.json"),
        "foto_perfil": os.path.join(base, "foto_perfil.jpg"),
        "seguidores": os.path.join(base, "seguidores.json"),
        "seguidos": os.path.join(base, "seguidos.json"),
    }


def relativa(ruta, rutas):
    """Ruta relativa a la carpeta del perfil, con '/' (portable entre sistemas)."""
    return os.path.relpath(ruta, rutas["base"]).replace(os.sep, "/")


def dimensiones_jpeg(datos):
    """(ancho, alto) leídos de los bytes de un JPEG, o None si no se pueden leer."""
    i = 2
    while i + 9 < len(datos):
        if datos[i] != 0xFF:
            i += 1
            continue
        marcador = datos[i + 1]
        if marcador in (0xD8, 0x01) or 0xD0 <= marcador <= 0xD7:
            i += 2
            continue
        longitud = int.from_bytes(datos[i + 2:i + 4], "big")
        if 0xC0 <= marcador <= 0xCF and marcador not in (0xC4, 0xC8, 0xCC):
            alto = int.from_bytes(datos[i + 5:i + 7], "big")
            ancho = int.from_bytes(datos[i + 7:i + 9], "big")
            return ancho, alto
        i += 2 + longitud
    return None


def sesion_activa(page):
    """True si Instagram nos reconoce como usuario con sesión iniciada."""
    page.goto("https://www.instagram.com/", timeout=60000)
    pausa(3, 5)
    en_login = "accounts/login" in page.url or page.locator("input[name='username']").count() > 0
    return not en_login


def obtener_contexto(browser):
    """Devuelve (context, page) con sesión: restaura cookies guardadas o pide login manual."""
    if os.path.exists(ARCHIVO_COOKIES):
        context = browser.new_context(storage_state=ARCHIVO_COOKIES, **OPCIONES_CONTEXTO)
        page = context.new_page()
        if sesion_activa(page):
            print("Sesión restaurada desde las cookies guardadas.")
            return context, page
        print("Las cookies guardadas ya no son válidas. Hay que iniciar sesión de nuevo.")
        context.close()

    # Primer uso (o cookies vencidas): login manual y guardado de cookies
    context = browser.new_context(**OPCIONES_CONTEXTO)
    page = context.new_page()
    page.goto("https://www.instagram.com/accounts/login/", timeout=60000)
    print("Inicia sesión manualmente en la ventana del navegador (cuenta de prueba).")
    input("Cuando veas tu feed de Instagram, presiona ENTER aquí... ")

    context.storage_state(path=ARCHIVO_COOKIES)
    nombres = sorted(c["name"] for c in context.cookies())
    print(f"Cookies guardadas en '{ARCHIVO_COOKIES}': {', '.join(nombres)}")
    return context, page


def descargar(context, url, ruta):
    """Descarga un archivo con la sesión del navegador. Devuelve los bytes o None si falla."""
    resp = context.request.get(url, headers={"Referer": "https://www.instagram.com/"})
    if resp.ok:
        datos = resp.body()
        with open(ruta, "wb") as f:
            f.write(datos)
        return datos
    print(f"   -> Error HTTP {resp.status} al descargar.")
    return None


def extraer_perfil(page, context, usuario, rutas):
    """Lee la cabecera del perfil: conteos, descripción y foto."""
    print(f"Leyendo datos del perfil @{usuario}...")
    page.goto(f"https://www.instagram.com/{usuario}/", timeout=60000)
    page.wait_for_selector("header", timeout=30000)
    pausa(2, 3.5)

    texto = page.locator("header").first.inner_text()
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]

    conteos = {"publicaciones": None, "seguidores": None, "seguidos": None}
    ultimo_conteo = -1
    for idx, linea in enumerate(lineas):
        m = PATRON_STAT.match(linea)
        if m:
            conteos[CLAVES_STAT[m.group(2).lower()]] = m.group(1).strip()
            ultimo_conteo = idx

    # Lo que aparece después de los conteos es el nombre visible y la biografía
    resto = [l for l in lineas[ultimo_conteo + 1:] if l.lower() not in TEXTOS_BOTONES]

    ruta_foto = "No disponible"
    imagenes = page.locator("header img")
    if imagenes.count() > 0:
        src = imagenes.first.get_attribute("src")
        if src and descargar(context, src, rutas["foto_perfil"]):
            ruta_foto = relativa(rutas["foto_perfil"], rutas)

    perfil = {
        "usuario": usuario,
        "url": f"https://www.instagram.com/{usuario}/",
        "seguidores": conteos["seguidores"],
        "seguidos": conteos["seguidos"],
        "publicaciones_total": conteos["publicaciones"],
        "descripcion_perfil": "\n".join(resto),
        "foto_perfil_local": ruta_foto,
        "texto_cabecera_crudo": lineas,   # para verificar/ajustar el análisis si algo no cuadra
    }
    with open(rutas["perfil"], "w", encoding="utf-8") as f:
        json.dump(perfil, f, ensure_ascii=False, indent=4)
    return perfil


def es_cuenta_propia(page):
    """True si el perfil abierto es el de la sesión iniciada (muestra 'Editar perfil')."""
    if page.locator("a[href='/accounts/edit/']").count() > 0:
        return True
    return page.get_by_text(re.compile(r"^(Editar perfil|Edit profile)$", re.IGNORECASE)).count() > 0


# Las listas de la ventana emergente se piden a este servicio interno de Instagram;
# leer sus respuestas no depende de cómo esté armado el HTML de la ventana.
PATRON_API_LISTA = re.compile(r"/api/v1/friendships/\d+/(followers|following)/")


def escuchar_api_listas(page, tipo):
    """Empieza a registrar las cuentas que Instagram devuelve al cargar la lista 'tipo'."""
    registro = {"usuarios": [], "estados": []}

    def al_responder(resp):
        m = PATRON_API_LISTA.search(resp.url)
        if not m or m.group(1) != tipo:
            return
        registro["estados"].append(resp.status)
        try:
            for u in resp.json().get("users", []):
                nombre = u.get("username")
                if nombre and nombre not in registro["usuarios"]:
                    registro["usuarios"].append(nombre)
        except Exception:
            pass

    page.on("response", al_responder)
    return registro, al_responder


def asegurar_perfil(page, usuario):
    """Vuelve al perfil si la página quedó en otra parte (por ejemplo, tras cerrar una lista)."""
    if not page.url.rstrip("/").endswith(f"/{usuario}"):
        page.goto(f"https://www.instagram.com/{usuario}/", timeout=60000)
    try:
        page.wait_for_selector("header", timeout=30000)
    except Exception:
        pass


def abrir_lista(page, usuario, tipo):
    """Intenta abrir la ventana de 'followers' o 'following'. Devuelve cómo lo logró, o None.

    1) clic en el contador de la cabecera ('26 seguidores', '64 seguidos', '12 following'...);
       el texto debe empezar por un número, así no se pulsa por error el botón 'Siguiendo'
       ni una frase de la biografía que mencione la palabra. Playwright espera hasta 15 s
       a que el contador exista.
    2) enlace con href .../<tipo>/ (por si en tu sesión sí es un enlace)
    3) abrir directamente la URL /<usuario>/<tipo>/
    """
    etiquetas = "seguidores|followers" if tipo == "followers" else "seguidos|following"
    contador = page.locator("header").get_by_text(
        re.compile(rf"^[\d.,]+\s?(mil|[KkMm])?\s+({etiquetas})\b", re.IGNORECASE)).first
    try:
        contador.click(timeout=15000)
        return "texto del contador"
    except Exception:
        pass

    selector = f"a[href='/{usuario}/{tipo}/'], a[href$='/{usuario}/{tipo}/'], a[href*='/{tipo}/']"
    try:
        page.wait_for_selector(selector, timeout=3000)
        page.locator(selector).first.click()
        return "enlace"
    except Exception:
        pass

    try:
        page.goto(f"https://www.instagram.com/{usuario}/{tipo}/", timeout=60000)
        page.wait_for_selector("div[role='dialog']", timeout=10000)
        return "URL directa"
    except Exception:
        asegurar_perfil(page, usuario)   # volver al perfil para que el diagnóstico muestre la página correcta
        return None


def diagnosticar_perfil(page, usuario, tipo):
    """Guarda una captura y muestra qué está viendo realmente el navegador."""
    ruta_png = os.path.join(CARPETA_DATOS, usuario, f"debug_{tipo}.png")
    try:
        page.screenshot(path=ruta_png)
    except Exception:
        pass
    try:
        enlaces_follow = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.getAttribute('href')).filter(h => /follow/i.test(h))")
    except Exception:
        enlaces_follow = []
    privada = page.get_by_text(re.compile(r"cuenta es privada|account is private", re.IGNORECASE)).count() > 0
    print(f"      URL actual: {page.url}")
    print(f"      Cuenta privada: {'sí' if privada else 'no se detecta'} | enlaces con 'follow': {enlaces_follow[:5]}")
    print(f"      Captura de diagnóstico: {ruta_png}")


def extraer_lista(page, usuario, tipo, maximo):
    """Abre la lista 'followers' o 'following' de un perfil y devuelve sus nombres de usuario.

    Funciona con cualquier perfil que tu sesión pueda ver (público, o privado que sigues).
    Combina dos fuentes: los enlaces que se ven en la ventana y las respuestas de red que
    Instagram envía al cargar la lista. Si algo falla, guarda una captura y datos de
    diagnóstico en datos/<usuario>/debug_<tipo>.png
    """
    asegurar_perfil(page, usuario)
    registro, manejador = escuchar_api_listas(page, tipo)

    via = abrir_lista(page, usuario, tipo)
    if via is None:
        page.remove_listener("response", manejador)
        print(f"   !! No se pudo abrir la lista de {tipo}: no hay enlace ni contador que se pueda pulsar.")
        diagnosticar_perfil(page, usuario, tipo)
        return []
    print(f"   -> Lista de {tipo} abierta (vía: {via})")

    # La lista carga con retraso: esperar a que aparezcan enlaces de perfil dentro de un diálogo.
    selector_lista = "div[role='dialog'] a[href^='/'], div[role='dialog'] a[href*='instagram.com/']"
    try:
        page.wait_for_selector(selector_lista, timeout=20000)
    except Exception:
        print(f"   !! El diálogo de {tipo} no mostró ninguna cuenta (¿lista vacía, privada o aún cargando?).")
    dialogo = page.locator("div[role='dialog']").filter(has=page.locator("a[href]")).first
    if dialogo.count() == 0:
        dialogo = page.locator("div[role='dialog']").first
    pausa(2, 3)

    vistos, sin_cambio, hrefs = [], 0, []
    while len(vistos) < maximo and sin_cambio < 10:
        antes = len(vistos)
        try:
            hrefs = dialogo.locator("a[href]").evaluate_all("els => els.map(e => e.getAttribute('href'))")
        except Exception:
            hrefs = []
        candidatos = []
        for h in hrefs:
            ruta = urlparse(h or "").path           # funciona con '/usuario/', '/usuario' y URLs completas
            m = re.fullmatch(r"/([A-Za-z0-9._]+)/?", ruta)
            if m:
                candidatos.append(m.group(1))
        candidatos += registro["usuarios"]           # lo que llegó por la red
        for c in candidatos:
            if c not in RUTAS_RESERVADAS and c != usuario and c not in vistos:
                vistos.append(c)
        sin_cambio = sin_cambio + 1 if len(vistos) == antes else 0
        print(f"   -> {tipo}: {len(vistos)} cuentas leídas")
        try:
            dialogo.evaluate(JS_SCROLL_DIALOGO)
            dialogo.hover(timeout=2000)              # respaldo: rueda del ratón sobre el diálogo
            page.mouse.wheel(0, 1500)
        except Exception:
            pass
        pausa(1.5, 2.5)

    page.remove_listener("response", manejador)

    if not vistos:
        n_dialogos = page.locator("div[role='dialog']").count()
        print(f"   !! No se leyó ninguna cuenta de {tipo}. Diálogos: {n_dialogos} "
              f"| enlaces en el diálogo: {len(hrefs)} | muestra de hrefs: {hrefs[:5]}")
        print(f"      Respuestas de red de la lista: {registro['estados'] or 'ninguna'} "
              f"(200 = Instagram respondió; 4xx = rechazó la petición)")
        diagnosticar_perfil(page, usuario, tipo)

    page.keyboard.press("Escape")
    pausa(1, 2)
    return [{"usuario": u, "url": f"https://www.instagram.com/{u}/"} for u in vistos[:maximo]]


def abrir_navegador(p):
    """Google Chrome real si está instalado (como el Selenium del script de tu compañero);
    si no, el Chromium que trae Playwright."""
    try:
        return p.chromium.launch(channel="chrome", headless=False)
    except Exception:
        print("Google Chrome no encontrado: se usa el Chromium de Playwright.")
        return p.chromium.launch(headless=False)


def recolectar_enlaces(page, usuario, rutas, cantidad):
    if os.path.exists(rutas["enlaces"]):
        with open(rutas["enlaces"], encoding="utf-8") as f:
            enlaces = json.load(f)
        if len(enlaces) >= cantidad:
            print(f"Usando {len(enlaces)} enlaces guardados en caché.")
            return enlaces[:cantidad]

    if not page.url.rstrip("/").endswith(f"/{usuario}"):
        page.goto(f"https://www.instagram.com/{usuario}/", timeout=60000)
    selector = "a[href*='/p/'], a[href*='/reel/']"
    page.wait_for_selector(selector, timeout=30000)

    enlaces = []
    for _ in range(12):  # máximo de scrolls
        hrefs = page.eval_on_selector_all(selector, "els => els.map(e => e.href)")
        for h in hrefs:
            limpio = h.split("?")[0]
            if limpio not in enlaces:
                enlaces.append(limpio)
        print(f"   -> Enlaces capturados: {len(enlaces)}/{cantidad}")
        if len(enlaces) >= cantidad:
            break
        page.mouse.wheel(0, 1200)
        pausa(2, 3.5)

    enlaces = enlaces[:cantidad]
    with open(rutas["enlaces"], "w", encoding="utf-8") as f:
        json.dump(enlaces, f, indent=2)
    return enlaces


def leer_meta(page, propiedad):
    loc = page.locator(f"meta[property='{propiedad}']")
    return loc.first.get_attribute("content") if loc.count() > 0 else None


def extraer_imagenes_post(page, context, i, rutas):
    """Descarga las imágenes completas de la publicación (todas las del carrusel).

    Se leen del propio post en pantalla, no de og:image: esa metaetiqueta apunta a una
    miniatura que Instagram recorta a cuadrado.
    """
    try:
        page.wait_for_selector("article img", timeout=10000)
    except Exception:
        pass

    guardadas, vistas = [], set()
    for _ in range(MAX_IMAGENES_POR_POST):
        info = page.evaluate(JS_MEJOR_IMAGEN)
        if not info:
            break
        clave = info["url"].split("?")[0]
        if clave in vistas:
            break
        vistas.add(clave)

        n = len(guardadas) + 1
        ruta = os.path.join(rutas["imagenes"], f"post_{i}_{n}.jpg")
        datos = descargar(context, info["url"], ruta)
        if datos:
            ancho, alto = dimensiones_jpeg(datos) or (info["ancho"], info["alto"])
            guardadas.append({"archivo": relativa(ruta, rutas), "ancho": ancho, "alto": alto})
            print(f"   -> Imagen {n} guardada: {ruta} ({ancho}x{alto})")

        siguiente = page.locator("article button[aria-label='Siguiente'], article button[aria-label='Next']")
        if siguiente.count() == 0:
            break
        siguiente.first.click()
        pausa(0.8, 1.5)
    return guardadas


def procesar_publicacion(page, context, i, url, rutas):
    page.goto(url, timeout=60000)
    page.wait_for_load_state("domcontentloaded")
    pausa(2.5, 4.5)

    if "accounts/login" in page.url:
        raise RuntimeError("Instagram pidió login: la sesión se perdió o hay un bloqueo temporal.")

    titulo = leer_meta(page, "og:title") or "Sin descripción"
    resumen = leer_meta(page, "og:description") or ""   # suele incluir likes/comentarios

    imagenes = extraer_imagenes_post(page, context, i, rutas)
    nota = ""
    if not imagenes:
        # Plan B: miniatura de og:image (puede salir recortada). Típico en videos/reels.
        img_url = leer_meta(page, "og:image")
        if img_url:
            ruta = os.path.join(rutas["imagenes"], f"post_{i}_1.jpg")
            datos = descargar(context, img_url, ruta)
            if datos:
                ancho, alto = dimensiones_jpeg(datos) or (None, None)
                imagenes.append({"archivo": relativa(ruta, rutas), "ancho": ancho, "alto": alto})
                nota = "Miniatura og:image (puede estar recortada)"
                print(f"   -> Solo se obtuvo la miniatura: {ruta}")

    return {
        "id": i,
        "url": url,
        "descripcion": titulo,
        "resumen": resumen,
        "imagenes": imagenes,
        "nota": nota,
    }


def pedir_argumentos():
    parser = argparse.ArgumentParser(description="Scraping de un perfil público de Instagram.")
    parser.add_argument("usuario", nargs="?", help="nombre de usuario (con o sin @)")
    parser.add_argument("--cantidad", type=int, default=CANTIDAD_POR_DEFECTO,
                        help=f"número de publicaciones (por defecto {CANTIDAD_POR_DEFECTO})")
    parser.add_argument("--listas", action="store_true",
                        help="guardar también las listas de seguidores y seguidos del perfil")
    parser.add_argument("--max-lista", type=int, default=MAX_LISTA_POR_DEFECTO,
                        help=f"máximo de cuentas por lista (por defecto {MAX_LISTA_POR_DEFECTO})")
    args = parser.parse_args()

    texto = args.usuario or input("Usuario de Instagram a analizar (sin @): ")
    try:
        usuario = normalizar_usuario(texto)
    except ValueError as e:
        parser.error(str(e))
    return usuario, max(1, args.cantidad), args.listas, max(1, args.max_lista)


def main():
    usuario, cantidad, listas, max_lista = pedir_argumentos()
    rutas = preparar_rutas(usuario)
    print(f"Perfil: @{usuario} | Publicaciones: {cantidad} | Carpeta: {rutas['base']}\n")

    with sync_playwright() as p:
        browser = abrir_navegador(p)
        try:
            context, page = obtener_contexto(browser)

            try:
                perfil = extraer_perfil(page, context, usuario, rutas)
                print(f"   -> Seguidores: {perfil['seguidores']} | Seguidos: {perfil['seguidos']} "
                      f"| Publicaciones: {perfil['publicaciones_total']}")
            except Exception as e:
                print(f"   !! No se pudo leer el perfil, se continúa con las publicaciones: {e}")

            if listas:
                print(f"Extrayendo listas de seguidores/seguidos para @{usuario}...")
                for tipo, clave in (("followers", "seguidores"), ("following", "seguidos")):
                    try:
                        cuentas = extraer_lista(page, usuario, tipo, max_lista)
                        with open(rutas[clave], "w", encoding="utf-8") as f:
                            json.dump(cuentas, f, ensure_ascii=False, indent=4)
                        print(f"   -> {len(cuentas)} {clave} guardados en {rutas[clave]}")
                    except Exception as e:
                        print(f"   !! No se pudo leer la lista de {clave}: {e}")

            try:
                enlaces = recolectar_enlaces(page, usuario, rutas, cantidad)
            except Exception as e:
                print(f"\nNo se encontraron publicaciones de @{usuario}. ¿El usuario existe y es público? ({e})")
                return
            print(f"\nProcesando {len(enlaces)} publicaciones.\n")

            resultados = []
            for i, url in enumerate(enlaces, 1):
                print(f"[{i}/{len(enlaces)}] {url}")
                try:
                    resultados.append(procesar_publicacion(page, context, i, url, rutas))
                except RuntimeError as e:
                    print(f"   !! {e} Se detiene para no empeorar el bloqueo.")
                    break
                except Exception as e:
                    print(f"   !! Error en esta publicación, se continúa: {e}")

                # Guardado incremental: si algo falla no pierdes lo ya obtenido
                with open(rutas["publicaciones"], "w", encoding="utf-8") as f:
                    json.dump(resultados, f, ensure_ascii=False, indent=4)

                if i % 5 == 0 and i != len(enlaces):
                    pausa_aleatoria = random.uniform(7.0, 14.0)
                    print("   --- Pausa aleatoria ---")
                    time.sleep(pausa_aleatoria)

            print(f"\nListo: {len(resultados)} publicaciones de @{usuario} guardadas en '{rutas['base']}/'")
        finally:
            browser.close()


if __name__ == "__main__":
    main()