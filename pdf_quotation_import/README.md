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
