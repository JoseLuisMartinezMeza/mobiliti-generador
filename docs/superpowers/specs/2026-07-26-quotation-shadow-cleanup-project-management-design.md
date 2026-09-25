# Diseño: limpieza de sombras y gestión segura de Proyectos

**Fecha:** 2026-07-26
**Estado:** Aprobado por el usuario

## Objetivo

1. Limpiar las imágenes importadas desde una hoja `Quotation` antes de insertarlas en
   `Cotizacion`, eliminando fondo y sombras de piso oscuras sin perder patas, ruedas,
   cables ni otros detalles delgados del producto.
2. Permitir renombrar un Proyecto desde la vista de Proyectos.
3. Permitir eliminar definitivamente un Proyecto únicamente cuando ya esté archivado.

## Alcance de imágenes

- La hoja `Quotation` copiada al libro final permanece intacta.
- La limpieza sólo afecta la copia visual usada en la hoja `Cotizacion`.
- Las imágenes de catálogo no se reprocesan con la nueva segmentación; sólo las líneas
  cuyo origen sea una importación de `Quotation`.
- El resultado interno usa PNG con transparencia real. La composición final conserva el
  objeto sin sombra sobre el fondo blanco natural de la celda.

## Flujo de segmentación

1. Decodificar la imagen y normalizar orientación y color.
2. Si la imagen ya tiene transparencia útil, conservarla y limitarse a limpiar bordes.
3. Ejecutar segmentación local no generativa con `rembg` y el modelo `silueta`.
4. Recuperar estructuras finas conectadas al objeto mediante bordes y contraste local,
   sin recuperar regiones anchas de sombra.
5. Limpiar halos claros del contorno y recortar márgenes transparentes.
6. Aplicar controles de calidad al área visible, caja envolvente y retención del objeto.
7. Si el modelo no está disponible o la máscara falla los controles, usar el procesador
   determinista existente como fallback para no bloquear la cotización.

No se usa un servicio externo ni generación visual: la forma y los colores del producto
deben provenir exclusivamente de la imagen original.

## Renombrado

- Cada tarjeta de Proyecto tendrá una acción `Renombrar`.
- La tarjeta cambia a un campo de edición con `Guardar` y `Cancelar`.
- El guardado reutiliza el `PATCH /projects/{id}` existente, junto con la revisión
  esperada y un `operation_id`.
- Se modifica únicamente `project.name`; los datos comerciales de una cotización ya
  capturados no se sobrescriben.

## Eliminación definitiva

- La acción `Eliminar definitivamente` sólo aparece en la lista de archivados.
- La interfaz solicita escribir el nombre exacto del Proyecto.
- El backend vuelve a verificar propietario, estado `archived`, revisión y nombre exacto.
- Si cualquiera de esas condiciones cambió, la operación falla sin borrar nada.
- La eliminación quita el registro del Proyecto. No elimina archivos de imágenes ni
  activos compartidos, porque proyectos duplicados pueden referenciarlos.
- Si el Proyecto eliminado todavía estaba cargado como activo en el navegador, se limpia
  el estado local para impedir un autoguardado posterior sobre un registro inexistente.

## Errores y observabilidad

- Los fallos de segmentación no cancelan la generación; activan el fallback.
- Los conflictos de revisión en renombrado o eliminación se muestran como errores
  recuperables y fuerzan una actualización de la lista.
- La eliminación de un Proyecto activo devuelve un mensaje explícito indicando que debe
  archivarse primero.

## Validación

- Pruebas unitarias del limpiador con sombra, transparencia y estructuras finas.
- Prueba del motor que confirma que la nueva segmentación sólo se solicita para líneas
  importadas.
- Pruebas API para eliminación archivada, activa, nombre incorrecto, revisión obsoleta y
  aislamiento entre usuarios.
- Pruebas de interfaz para edición del nombre y disponibilidad de la eliminación sólo en
  archivados.
- Validación visual con las tres imágenes reales proporcionadas por el usuario.
