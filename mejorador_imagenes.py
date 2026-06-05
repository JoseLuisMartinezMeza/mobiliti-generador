#!/usr/bin/env python3
"""
mejorador_imagenes.py
=====================
Pipeline de mejora de imágenes para cotizaciones Mobiliti.
Usa Pillow (ya instalado) para procesamiento ligero 100% offline.

Pipeline por imagen:
    1. Auto-contraste (ImageOps.autocontrast)
    2. Trim de bordes blancos/casi-blancos
    3. Fondo blanco uniforme (#FFFFFF)
    4. Sharpening ligero
    5. Caché por hash SHA256

Uso:
    from mejorador_imagenes import mejorar_image_map
    image_map = mejorar_image_map(image_map, temp_dir)
"""

import hashlib
import os
import shutil
import time
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageFilter, ImageOps
    PIL_DISPONIBLE = True
except ImportError:
    PIL_DISPONIBLE = False


# Umbral para considerar un pixel como "blanco" en el trim (0-255)
UMBRAL_BLANCO_TRIM = 250


def _calcular_hash_contenido(path: str) -> str:
    """Calcula SHA256 del contenido binario de la imagen."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _ruta_cache(img_hash: str, cache_dir: str) -> str:
    """Devuelve la ruta donde se guardaria la imagen mejorada en caché."""
    return os.path.join(cache_dir, f"{img_hash}.png")


def _limpiar_cache_antigua(cache_dir: str, dias: int = 30):
    """Elimina archivos de caché más antiguos que N días."""
    if not os.path.exists(cache_dir):
        return
    limite = time.time() - (dias * 86400)
    for fname in os.listdir(cache_dir):
        fpath = os.path.join(cache_dir, fname)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < limite:
            try:
                os.remove(fpath)
            except OSError:
                pass


def _trim_bordes_blancos(img: Image.Image) -> Image.Image:
    """
    Recorta bordes blancos o casi-blancos de la imagen.
    Si la imagen tiene canal alpha, recorta basándose en la transparencia.
    Devuelve la imagen recortada o la original si no hay bordes.
    """
    if img.mode == "RGBA":
        # Usar canal alpha para encontrar el bounding box del contenido visible
        alpha = img.split()[-1]
        mask = alpha.point(lambda p: 255 if p > 10 else 0, mode="1")
    else:
        # Convertir a escala de grises para análisis de un solo canal
        gray = img.convert("L")
        # Crear máscara binaria: 255 donde el pixel NO es blanco (< umbral)
        mask = gray.point(lambda p: 255 if p < UMBRAL_BLANCO_TRIM else 0, mode="1")
    # Encontrar bounding box del contenido no-blanco/no-transparente
    bbox = mask.getbbox()
    if bbox:
        return img.crop(bbox)
    return img


def _quitar_fondo_blanco(img: Image.Image, umbral: int = 240) -> Image.Image:
    """
    Hace transparentes los píxeles blancos o cercanos al blanco.
    Devuelve imagen RGBA con fondo transparente.
    """
    # Asegurar RGBA
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Separar canales
    r, g, b, a = img.split()

    # Crear máscaras individuales para cada canal (modo L, 0 o 255)
    mask_r = r.point(lambda p: 255 if p > umbral else 0)
    mask_g = g.point(lambda p: 255 if p > umbral else 0)
    mask_b = b.point(lambda p: 255 if p > umbral else 0)

    # Combinar máscaras (AND lógico via multiply): 255 solo donde R, G y B > umbral
    mask_blanco = ImageChops.multiply(mask_r, mask_g)
    mask_blanco = ImageChops.multiply(mask_blanco, mask_b)

    # Invertir: ahora 255 = contenido (no blanco), 0 = blanco (transparente)
    mask_visible = ImageChops.invert(mask_blanco)

    # Aplicar máscara al canal alpha existente
    nuevo_alpha = ImageChops.multiply(a, mask_visible)

    # Recomponer imagen con nuevo alpha
    img.putalpha(nuevo_alpha)
    return img


def _aplicar_fondo_blanco(img: Image.Image) -> Image.Image:
    """
    Componer la imagen sobre un fondo blanco puro (#FFFFFF).
    Si la imagen tiene transparencia, se respeta.
    NOTA: Esta funcion ya no se usa en el pipeline por defecto;
    se mantiene por compatibilidad si se necesita fondo blanco.
    """
    if img.mode in ("RGBA", "LA", "P"):
        # Convertir paleta a RGBA si es necesario
        if img.mode == "P":
            img = img.convert("RGBA")
        # Crear fondo blanco del mismo tamaño
        fondo = Image.new("RGBA", img.size, (255, 255, 255, 255))
        # Componer imagen sobre fondo blanco
        resultado = Image.alpha_composite(fondo, img)
        return resultado.convert("RGB")
    elif img.mode != "RGB":
        return img.convert("RGB")
    return img


def _mejorar_imagen_pillow(img_path: str, output_path: str) -> bool:
    """
    Aplica el pipeline de mejora Pillow a una imagen.
    Quita el fondo blanco y deja transparencia.
    Guarda el resultado en output_path como PNG RGBA.
    Devuelve True si tuvo éxito, False en caso contrario.
    """
    try:
        with Image.open(img_path) as img:
            # 1. Auto-contraste (sobre RGB para evitar problemas con alpha)
            if img.mode == "RGBA":
                # Aplicar autocontrast solo a los canales RGB
                rgb = img.convert("RGB")
                rgb = ImageOps.autocontrast(rgb, cutoff=0)
                r, g, b = rgb.split()
                a = img.split()[-1]
                img = Image.merge("RGBA", (r, g, b, a))
            else:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img = ImageOps.autocontrast(img, cutoff=0)

            # 2. Quitar fondo blanco (hacer transparente)
            img = _quitar_fondo_blanco(img)

            # 3. Trim de bordes transparentes
            img = _trim_bordes_blancos(img)

            # 4. Sharpening ligero
            img = img.filter(ImageFilter.SHARPEN)

            # 5. Guardar como PNG con canal alpha
            img.save(output_path, "PNG", optimize=True)
            return True
    except Exception:
        return False


def obtener_imagen_mejorada(img_path: str, cache_dir: str) -> str:
    """
    Obtiene la versión mejorada de una imagen, usando caché si está disponible.
    Si el procesamiento falla, devuelve la ruta original.

    Args:
        img_path: Ruta a la imagen original.
        cache_dir: Directorio donde se almacena la caché.

    Returns:
        Ruta a la imagen mejorada (o original si falla).
    """
    if not PIL_DISPONIBLE or not os.path.exists(img_path):
        return img_path

    # Asegurar que existe el directorio de caché
    os.makedirs(cache_dir, exist_ok=True)

    # Calcular hash del contenido
    img_hash = _calcular_hash_contenido(img_path)
    cache_path = _ruta_cache(img_hash, cache_dir)

    # Si existe en caché, devolver directamente
    if os.path.exists(cache_path):
        return cache_path

    # Procesar y guardar en caché
    if _mejorar_imagen_pillow(img_path, cache_path):
        return cache_path

    # Si falló el procesamiento, devolver original
    return img_path


def mejorar_image_map(image_map: dict, temp_dir: str) -> dict:
    """
    Mejora todas las imágenes de un image_map usando Pillow + caché.

    Args:
        image_map: Diccionario {fila: ruta_imagen}.
        temp_dir: Directorio temporal del proyecto (se crea subdir 'mejoradas').

    Returns:
        Nuevo diccionario {fila: ruta_imagen_mejorada}.
    """
    if not PIL_DISPONIBLE:
        return image_map

    cache_dir = os.path.join(temp_dir, "mejoradas")
    os.makedirs(cache_dir, exist_ok=True)

    # Limpieza ligera de caché muy antigua (solo si hay imágenes que procesar)
    if image_map:
        _limpiar_cache_antigua(cache_dir, dias=30)

    mejorado = {}
    total = len(image_map)
    for idx, (row, img_path) in enumerate(image_map.items(), 1):
        if os.path.exists(img_path):
            mejorada = obtener_imagen_mejorada(img_path, cache_dir)
            mejorado[row] = mejorada
            if mejorada != img_path:
                print(f"      [{idx}/{total}] Imagen mejorada (fila {row})")
            else:
                print(f"      [{idx}/{total}] Imagen sin cambios (fila {row})")
        else:
            mejorado[row] = img_path

    return mejorado
