# Instagram Profile & Media Scraper (Playwright)

Herramienta de extracción de datos y automatización web desarrollada en **Python** utilizando **Playwright**. Diseñada para realizar web scraping eficiente y estructurado sobre perfiles públicos de Instagram, extrayendo metadatos de perfiles, publicaciones en alta resolución (incluyendo carruseles completos) y listados interactivos de seguidores/seguidos.

---

## 🚀 Características Técnicas

- **Automatización con Playwright:** Ejecución basada en un motor Chromium optimizado para evitar bloqueos automatizados.
- **Autenticación Persistente (`Storage State`):** Sistema de gestión de sesiones mediante el almacenamiento seguro de cookies (`cookies_sesion.json`), eliminando la necesidad de reautenticaciones manuales constantes.
- **Extracción de Carruseles en Alta Resolución:** Algoritmo dinámico que analiza el DOM y los atributos `srcset` para capturar la máxima calidad de imagen disponible, omitiendo miniaturas recortadas.
- **Scroll Inteligente y Dinámico:** Extracción masiva de seguidores y seguidos mediante desplazamiento automatizado de cuadros modales con control de excepciones.
- **Persistencia Estructurada:** Organiza automáticamente los resultados en directorios aislados por usuario con archivos JSON independientes y recursos multimedia clasificados.

---

## 📁 Estructura del Proyecto

Los datos extraídos se almacenan de forma jerárquica bajo el esquema:

```text
datos/
└── <usuario>/
    ├── perfil.json              # Metadatos del perfil (biografía, contadores)
    ├── publicaciones.json       # Detalle y descripciones de los posts
    ├── enlaces.json             # Caché de URLs de publicaciones
    ├── foto_perfil.jpg          # Imagen de perfil descargada
    ├── seguidores.json          # Listado de seguidores (opcional)
    ├── seguidos.json            # Listado de seguidos (opcional)
    └── imagenes/                # Contenedor de recursos multimedia (post_n_m.jpg)
```

## 🛠️ Requisitos del Sistema

Python (Versión 3.8 o superior).

Entorno virtual recomendado (venv).

Instalación de Dependencias

# Ejecuta los siguientes comandos en tu terminal para configurar el entorno:
Clonar o posicionarse en el directorio del proyecto
Instalar Playwright y sus dependencias de navegador
**pip install playwright**
**playwright install chromium**

## 💻 Manual de Uso

El script cuenta con una interfaz de línea de comandos (argparse) flexible que permite múltiples formas de ejecución:
## 1. Ejecución Básica (Por defecto: 25 publicaciones)
 **python scraper_instagram.py _nombre_usuario_ **
Ejemplo:
python scraper_instagram.py nasa
## 2. Especificar Cantidad de Publicaciones
 **python scraper_instagram.py _nombre_usuario_ --cantidad _numero_**
Ejemplo:
python scraper_instagram.py nasa --cantidad 30
## 3. Extracción de Listas (Seguidores y Seguidos)
Permite extraer los listados interactivos indicando un límite máximo de cuentas:
**python scraper_instagram.py --listas --max-lista _numero_ _nombre_usuario_**
Ejemplo:
python scraper_instagram.py --listas --max-lista 10 tu_cuenta_arte
## 4. Modo Interactivo
Si ejecutas el script sin argumentos de usuario, la aplicación te solicitará el objetivo directamente por consola:
python scraper_instagram.py

## 🔒 Consideraciones de Seguridad y Buenas Prácticas

El archivo de sesión de cookies y variables de entorno estan exluidas mediante .gitignore.Mitigación Anti-Bot: Se han implementado tiempos de espera aleatorios (random.uniform) y pausas periódicas en los bucles de procesamiento para simular un comportamiento humano y minimizar riesgos de restricciones (rate limiting).

## 📌 Nota Académica

Proyecto desarrollado estrictamente con fines académicos e investigativos en el marco de la asignatura de Arquitectura de Software correspondiente del séptimo semestre. El uso de esta herramienta debe ajustarse de manera responsable a las políticas de uso de la plataforma.







