# PDF quotation import

Convierte una proforma PDF del proveedor al layout de `Quotation` que ya consume el motor existente de Mobiliti.

Uso:

```powershell
python -m pdf_quotation_import `
  --source "C:\Users\pepem\Downloads\6-22 4th revised  AL-ESP900-9122.pdf" `
  --reference-xlsx "C:\Users\pepem\Downloads\CET PRUEBAS GENERADOR-Quotation Sheet - V1.xlsx" `
  --output "$env:TEMP\AL-ESP900-9122-Quotation-from-pdf.xlsx"
```

La salida conserva la hoja `Quotation`, encabezados en fila 7, categorias en columna A con prefijo `-`, productos numerados, formulas de totales y fotos ancladas a la fila del producto.

## Respaldo con Claude para entradas no estandar

`llm_fallback.py` cubre los PDF y XLSX que el convertidor y el parser no entienden. Claude solo extrae los datos; la hoja `Quotation` la escribe el mismo `write_workbook` de arriba.

```powershell
python -m pdf_quotation_import.llm_fallback "C:\ruta\cotizacion.pdf" --modelo haiku
```

Requiere `ANTHROPIC_API_KEY` en el entorno y el paquete `anthropic`. Imprime productos, fotos asignadas, advertencias, tokens y `costo_usd` de la llamada. `--modelo` acepta `haiku` (Claude Haiku 5.5) o `sonnet` (Claude Sonnet 5.5).

En el worker se activa con `QUOTE_LLM_FALLBACK_MODEL=haiku` o `sonnet`; sin la variable el worker se comporta igual que antes. Para convertir, solo llama al modelo cuando el convertidor PDF falla o el XLSX no trae hoja `Quotation` con productos, y guarda tokens y costo en `metadata.llm_fallback` del job. Con la misma variable, al generar la cotización el motor consulta la categoría de los productos importados cuyo nombre no reconoce el diccionario, enviando también su foto ([detalle](../docs/2026-10-13-velocidad-y-clasificacion.md)).

La API lee la misma variable: con ella, "Previsualizar e importar al proyecto" acepta PDF y Excel sin hoja `Quotation`, los convierte y deja el borrador como si se hubiera subido la Quotation `.xlsx` (tokens y costo en `metadata.llm_fallback`). Requiere que el proceso de la API tenga `pdf_quotation_import`, PyMuPDF y `anthropic`; el bundle de Vercel no los trae, asi que ahi la variable debe seguir vacia.

Limites conocidos: no infiere proveedor ni tipo de cambio para el motor (la moneda detectada solo se reporta), las fotos se asignan unicamente cuando la pagina o fila trae tantas como productos, y una sola llamada cubre unas 80 partidas.

## Combinar varias cotizaciones para un Proyecto

Un Proyecto admite una sola Quotation de origen. `combinar.py` junta varias fuentes en una sola hoja `Quotation` y deja el proveedor y la moneda de cada renglon en las columnas `Supplier` y `Original Currency`, que el importador de Proyectos ya lee; asi cada linea conserva su tipo de cambio.

```powershell
python -m pdf_quotation_import.combinar --salida combinada.xlsx `
  --fuente "fabrica-Quotation.xlsx" Sunon USD `
  --fuente "proveedor-local.pdf" "Proveedor local" MXN
```

Cada `--fuente` lleva archivo, proveedor y moneda (`MXN`, `USD` o `EUR`). La primera es la base y se conserva tal cual; las demas se agregan al final con sus secciones y fotos. Una Quotation estandar se usa directo y sin costo; un PDF o un Excel con otro formato pasa antes por el respaldo con Claude. Antes de entregar el archivo se valida con el mismo `build_import_manifest` que usa Proyectos, y si no pasa no se escribe la salida.

Limites conocidos: el proveedor debe coincidir con un nombre de la hoja `Proveedores` de la plantilla o el motor lo trata como `Proveedor Externo`; volver a importar una combinada desde "Nueva" en un Proyecto que ya tenia lineas importadas las reemplaza y se pierden sus ediciones; los totales de la base no suman los renglones agregados.

### Importar otra Quotation desde el editor del Proyecto

La pestaña "Importar otra Quotation" del editor agrega una cotizacion mas sin perder lo ya editado. La web llama `POST /cotizaciones/{job}/import-preview?append_to_project={proyecto}`: la API anexa los renglones nuevos al final de la fuente guardada del Proyecto (sin columnas de proveedor ni moneda, porque esos datos viven en cada linea), deja esa combinada como entrada del borrador y responde la vista previa con `added_rows`. La web conserva las lineas importadas existentes, las revincula a la fuente combinada por numero de fila y crea lineas solo para `added_rows`. El Proyecto sigue teniendo una sola Quotation de origen. Cuando el autoguardado confirma la importacion, la web descarta el borrador (ya no se necesita y cuenta para el limite de cotizaciones activas) y recarga el Proyecto para mostrar las fotos durables.

El proveedor se elige de un menu con los nombres que acepta la plantilla (`GET /proveedores-oficiales`, tomado de `OFFICIAL_MOBILITI_PROVIDERS` del motor), tanto al importar como al editar una linea importada. Si la API no ofrece la lista, el campo vuelve a ser texto libre.
