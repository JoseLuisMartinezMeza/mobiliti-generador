#!/usr/bin/env python3
"""
insertar_imagenes.py
====================
Función auxiliar para insertar imágenes de productos en la hoja Cotizacion
del template de cotización Mobiliti.

Uso:
    from insertar_imagenes import insertar_imagenes_cotizacion
    insertar_imagenes_cotizacion(ws_cot, items, image_map, start_row=16)
"""


def insertar_imagenes_cotizacion(ws_cot, items, image_map, start_row=16, categoria_map=None):
    """
    Inserta imagenes de productos en la columna B de la hoja Cotizacion.
    
    Args:
        ws_cot: Hoja 'Cotizacion' del workbook xlwings.
        items: Lista de items leidos del Quotation.
        image_map: Diccionario {fila_quotation: ruta_imagen}.
        start_row: Fila donde empiezan los productos (default 16).
        categoria_map: Diccionario {fila_quotation: categoria} para escala por categoria.
    
    Returns:
        int: Cantidad de imagenes insertadas.
    """
    # Factores de escala por categoria (% del tamano de la celda)
    ESCALAS = {
        'Escritorios-WorkStation': 0.95,
        'Mesas de Juntas': 0.95,
        'Silla': 0.75,
        'Sillones': 0.75,
        'Mesas de Apoyo': 0.50,
    }
    ESCALA_DEFAULT = 0.60
    # Eliminar imagenes existentes en Cotizacion (solo productos, no el logo del encabezado)
    print("      Eliminando imagenes previas...")
    for pic in ws_cot.pictures:
        try:
            # Preservar logo del encabezado (top < 500)
            if pic.top > 500:
                pic.delete()
        except Exception:
            pass
    
    # Insertar imagenes
    print("      Insertando imagenes...")
    img_count = 0
    current_row = start_row
    
    for item in items:
        if item['tipo'] == 'categoria':
            current_row += 1
            continue
        
        q_row = item['row']
        if q_row in image_map:
            try:
                img_path = image_map[q_row]
                cell = ws_cot.range(f'B{current_row}')
                
                # Insertar imagen temporalmente en (0,0) para obtener dimensiones originales
                pic = ws_cot.pictures.add(img_path, left=0, top=0)
                orig_width = pic.width
                orig_height = pic.height
                
                # Determinar escala segun categoria (default 60%)
                categoria = (categoria_map or {}).get(q_row)
                escala_pct = ESCALAS.get(categoria, ESCALA_DEFAULT)
                
                # Calcular tamano maximo (% de la celda segun categoria)
                max_width = cell.width * escala_pct
                max_height = cell.height * escala_pct
                
                # Calcular factor de escala manteniendo proporción
                scale_w = max_width / orig_width if orig_width > 0 else 1
                scale_h = max_height / orig_height if orig_height > 0 else 1
                scale = min(scale_w, scale_h)
                
                # Aplicar escala
                pic.width = orig_width * scale
                pic.height = orig_height * scale
                
                # Centrar en la celda
                pic.left = cell.left + (cell.width - pic.width) / 2
                pic.top = cell.top + (cell.height - pic.height) / 2
                
                img_count += 1
            except Exception:
                pass
        
        current_row += 1
    
    print(f"      [OK] {img_count} imagenes insertadas")
    return img_count
