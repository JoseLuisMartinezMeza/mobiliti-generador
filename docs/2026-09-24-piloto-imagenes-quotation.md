# Piloto de mejora de imágenes Quotation

Solicitud: comparar las mismas siete imágenes con GPT Image 2.5 y SeedVR2 de fal.ai,
y conservar cada resultado para reutilizarlo entre proyectos. El usuario confirmó
SeedVR2 el 24 de septiembre de 2026.

## Estado comprobado

- Preparadas siete imágenes únicas PNG de 150 × 150 de la hoja `Quotation` de
  `Cotizacion_GEELY_-_PALMAS_200-00031.xlsx` (once apariciones).
- SHA-256 del Excel original: `f008db7f708465d4ec322bbeffce5a635fa2973eefc43f851f1593f959b2d7e4`.
- **Piloto completado: 14/14 imágenes generadas**, siete con OpenAI y siete con
  SeedVR2, todas PNG de 1024 × 1024. Galería y resultados conservados en la biblioteca.
- OpenAI Developers creó la clave `Codex` en `Personal / Default project`, con
  vigencia elegida de siete días; guardada con confirmación en `.env.local`
  (ignorado por Git). La consulta del modelo devolvió acceso correcto.
- Dos intentos de edición sobre la primera imagen devolvieron HTTP 400; el segundo
  permitió identificar `billing_hard_limit_reached`. No se enviaron las otras seis.
  Solicitud de diagnóstico: `req_ccb679cc25a94d16b20038cbb14b7b56`.
  El usuario después confirmó crédito en ambas cuentas y autorizó continuar. Se
  reanudó explícitamente esa versión y se conservó el rechazo previo en su historial.
- La clave de fal.ai se leyó en memoria desde el archivo indicado por el usuario:
  `C:/Users/pepem/Downloads/ARMADO_DE_CARATULA_pdf_quote_worktree/API_fai.txt`.
  No se copió la clave a resultados, código ni documentación.
- Promedios observados por imagen: OpenAI 25,81 segundos, SeedVR2 6,49 segundos.
  Incluyen comunicación, espera y descarga; no representan un benchmark general.
- Validación: 12 pruebas locales aprobadas, integridad de 14 salidas comprobada,
  reutilización de las 14 versiones con red bloqueada, Excel fuente intacto.
- Revisión visual: SeedVR2 preservó mejor la apariencia original en esta muestra;
  OpenAI produjo un acabado más fotográfico pero reinterpretó la malla de Aveza,
  detalles de base/ruedas y niveladores de DT206. Ninguna versión se aprobó aún
  para producción. Las imágenes originales de 150 píxeles no permiten certificar
  que los detalles reconstruidos existan en el producto real.
- No se modificó el Excel ni el motor productivo. La integración automática de
  imágenes aprobadas en las cotizaciones queda pendiente de comparar los resultados.

## Ejecutar el piloto

Desde la raíz del repositorio, con Pillow y openpyxl existentes:

```powershell
python -m scripts.quotation_image_trial `
  --fuente 'C:\Users\pepem\Downloads\Cotizacion_GEELY_-_PALMAS_200-00031.xlsx' `
  --biblioteca 'output/biblioteca-imagenes-mobiliti/biblioteca.db' `
  --salida 'output/comparativa-ia-geely-20260924'
```

Este comando no llama a ninguna API. Agregar `--ejecutar ambos` genera hasta siete
versiones por proveedor; `--ejecutar openai` o `--ejecutar seedvr2` limita el proveedor.
Las claves se leen del entorno del proceso; nunca se escriben en código, informes,
SQLite o frontend. Configurarlas con un mecanismo local de secretos, sin pegarlas
en el chat. Usar una biblioteca distinta para cada cuenta; la ruta local no es una
frontera de autorización de un SaaS multiusuario.

La galería `comparativa.html` muestra originales y resultados al mismo tamaño visual,
con enlaces a su resolución real y metadatos. Lo pendiente se señala explícitamente.

## Parámetros y revisión

- OpenAI: `gpt-image-2.5-sunburst-2026-09-08`, edición, `high`, PNG, 1024 × 1024.
  Instrucciones conservadoras: conservar forma, color, ángulo, brazos, patas y ruedas.
- fal.ai: `fal-ai/seedvr/upscale/image`, factor `1024 / 150`, PNG,
  `noise_scale=0.1`, semilla `20260924`. La resolución real se registra en la respuesta;
  el proveedor podría redondear las dimensiones. En esta ejecución las siete
  respuestas fueron exactamente 1024 × 1024.
- Se conservan ambas versiones como candidatas. Evaluar fidelidad, no solo nitidez:
  estructuras de alambre, cantidad de ruedas, brazos, tejidos, tonos y proporciones.
  No tratar una reconstrucción inventada como una foto oficial del fabricante.

## Reutilización y costos

SQLite guarda originales, resultados PNG, SHA-256, modelo, parámetros, estado,
identificador remoto y uso reportado por OpenAI. La clave de una versión combina
el hash del original, proveedor y configuración. Mismos bytes con otro nombre o en
otro proyecto reutilizan la versión existente. Una recompresión del archivo genera
otro hash: no se hace identificación perceptual, para no confundir variantes.

Una restricción única reserva la solicitud antes de contactar al proveedor. Si se
interrumpe una llamada de OpenAI sin respuesta confirmada, se bloquea otro envío
automático para evitar un posible cobro duplicado. Hay que conciliarla en la consola
del proveedor. SeedVR2 conserva su solicitud de cola y consulta el mismo trabajo
al reanudar; tampoco vuelve a enviarlo. La galería se puede reconstruir desde SQLite.

Después de la revisión visual, `--aprobar ID_COMPLETO` registra la versión elegida.
`obtener_aprobada(db, original_bytes)` recupera esa imagen sin llamar a la IA.
El piloto no conecta aún esta función con el motor de generación de Excel. Para
producción faltan esa integración, el almacenamiento persistente privado por cuenta
y la autorización de despliegue correspondiente a este cambio.

No se presenta una estimación como gasto real: revisar el uso y facturación de cada
proveedor tras ejecutar la prueba. No hay generación automática al cotizar.

## Validación

```powershell
python -m pytest tests/test_quotation_image_trial.py -q
```

Cubre reutilización entre procesos/proyectos, aprobación explícita, bloqueo de
reintentos ambiguos, reservas únicas entre conexiones, separación de bibliotecas,
reanudación de SeedVR2 y rechazo de imágenes corruptas, con proveedores simulados.

Además se completaron las catorce llamadas de generación contra las APIs reales y
se inspeccionaron visualmente todas las salidas. El informe de revisión está en
`output/comparativa-ia-geely-20260924/informe.md`, con comprobaciones en
`verificacion.json`. La galería se verificó en el navegador.

Fuentes verificadas:
- [OpenAI GPT Image 2.5 Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst)
- [SeedVR2 API](https://fal.ai/models/fal-ai/seedvr/upscale/image/api)
- [Cola de fal.ai](https://fal.ai/docs/documentation/model-apis/inference/queue)
