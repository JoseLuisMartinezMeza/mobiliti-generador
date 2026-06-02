# Plan: Automatización de Generación de Cotizaciones Mobiliti

## Objetivo
Crear un script Python robusto y reutilizable que automatice el proceso de generar cotizaciones a partir de:
- **Input 1:** Quotation del proveedor (`IZA MONTERREY BH-Quotation Sheet - V1.xlsx`)
- **Input 2:** Template de cotización (`Formato Cotización 2026 GDL (1).xlsx`)
- **Output:** Cotización final con formato, fórmulas, imágenes y totales

## Análisis de la Estructura Actual

### Quotation del Proveedor (Input)
- Hoja única: `Quotation`
- Fila 7: Headers (No., Item Name, Photo, Description, Dimension, Color, Q'ty, Vol., Tot.Vol., Unit Price, Tot.Price, Remark)
- Categorías: filas donde A = "- NOMBRE CATEGORIA"
- Productos: filas donde A = número, B = Item Name, D = Description, E = Dimension, F = Color, G = Q'ty, J = Unit Price
- Imágenes embebidas en columna C

### Template (Input)
- Hojas: `Cotizacion`, `Mobiliti`, `Proveedores`, `Estrategia Comercial`, `Fletes`, etc.
- `Cotizacion`: Header (filas 1-14), tabla de productos (fila 15+), totales, términos y condiciones
- `Mobiliti`: Tabla de productos con precios de lista, estrategia de descuentos

### Ejemplo Objetivo (Output)
- Hojas: `Cotizacion`, `Mobiliti`, `Quotation`, `COSTO LUMBRO`, `Estrategia Comercial`, etc.
- `Quotation` copiada del proveedor (con columna L "Precio Venta" agregada manualmente)
- `Mobiliti` generada con datos de Quotation
- `Cotizacion` con fórmulas que referencian Quotation y Mobiliti

## Gaps Identificados en Script v3 Actual
1. **Categorías:** Usan texto literal en vez de `=Quotation!A{row}`
2. **Precios Unitarios:** Usan `=Quotation!J{row}` pero el ejemplo usa `=Mobiliti!F{row}` o precios hardcodeados
3. **Filas dinámicas:** No inserta filas nuevas, solo sobreescribe existentes
4. **Imágenes:** Mapeo de imágenes no siempre es correcto
5. **Hojas adicionales:** Solo copia Quotation, no otras hojas del template

## Fases de Implementación

### Fase 1: Refinar Engine Base (v4)
**Status:** `pending`

1.1. Crear script `generar_cotizacion_v4.py` basado en v3 con mejoras:
- Usar `openpyxl` (compatible Python 3.14) como engine principal
- Leer estructura completa de Quotation (categorías + productos)
- Extraer imágenes con mapeo preciso fila→imagen

1.2. Corregir fórmulas de Cotizacion:
- Categorías: `=Quotation!A{row}`
- Productos:
  - A (Código): `=Quotation!B{row}`
  - C (Descripción): `=Quotation!D{row}`
  - D (Medidas): `=Quotation!E{row}`
  - E (Cantidad): `=Quotation!G{row}`
  - F (P. UNIT): Configurable (`=Quotation!J{row}`, `=Quotation!L{row}`, o `=Mobiliti!F{row}`)
  - G (% DESC): `=G$19` (referencia fija a fila de descuento)
  - H (Descuento): `=F*G`
  - I (Subtotal): `=F-H`
  - J (Total): `=I*E`

1.3. Implementar inserción dinámica de filas:
- Detectar cuántas filas necesita la cotización
- Insertar filas en Cotizacion preservando formato del template
- Preservar merged cells y estilos

1.4. Generar Mobiliti:
- Limpiar área de datos
- Crear filas de categorías y productos con fórmulas
- Calcular subtotales por sección

### Fase 2: Manejo de Precios de Venta
**Status:** `pending`

2.1. Implementar estrategia de precios configurable:
- **Opción A (default):** Usar `=Quotation!J{row}` (precio del proveedor)
- **Opción B:** Usar `=Quotation!L{row}` (precio de venta manual en Quotation)
- **Opción C:** Usar `=Mobiliti!F{row}` (precio de lista Mobiliti)
- **Opción D:** Permitir archivo CSV/JSON con overrides de precios

2.2. Agregar fila "INCLUYE ELECTRIFICACIÓN" configurable (checkbox)

### Fase 3: Preservación de Formato y Hojas
**Status:** `pending`

3.1. Copiar todas las hojas relevantes del template:
- `Quotation` (del proveedor)
- `Cotizacion` (generada)
- `Mobiliti` (generada)
- `Proveedores`, `Estrategia Comercial`, etc. (del template)

3.2. Preservar formato:
- Fonts, colores, bordes, fills
- Merged cells
- Imágenes del header
- Validación de datos

### Fase 4: Testing y Validación
**Status:** `pending`

4.1. Probar con Quotation del proveedor original
4.2. Comparar visualmente con ejemplo objetivo
4.3. Validar que fórmulas calculan correctamente
4.4. Verificar imágenes se insertan en filas correctas
4.5. Probar escenarios edge case (0 productos, muchos productos, sin imágenes)

### Fase 5: Documentación y Usabilidad
**Status:** `pending`

5.1. Crear README con instrucciones de uso
5.2. Agregar validación de inputs (archivos existen, hojas correctas)
5.3. Mejorar mensajes de error
5.4. Opcional: Crear versión GUI simple con tkinter

## Decisiones Pendientes
1. **Fuente de precios de venta:** ¿Debe el script asumir que el usuario agregó columna L al Quotation, o debe calcular desde Mobiliti?
2. **Fila INCLUYE ELECTRIFICACIÓN:** ¿Siempre se agrega o es condicional?
3. **Hojas adicionales:** ¿Cuáles hojas del template siempre se copian y cuáles no?

## Criterios de Éxito
- [ ] Script genera archivo Excel que abre sin errores
- [ ] Hoja Cotizacion tiene fórmulas vivas (no valores estáticos)
- [ ] Imágenes de productos se muestran correctamente
- [ ] Totales calculan correctamente
- [ ] Formato visual coincide con ejemplo objetivo
- [ ] Proceso es 100% automatizado (un solo comando)
