# Imágenes Quotation: OpenAI hasta el tope y después SeedVR2

Decisión del usuario: utilizar los US$30 ya comprados en OpenAI y continuar con
SeedVR2 al alcanzar el límite. Recarga automática OpenAI todavía activa; el
usuario indica que la tarjeta está bloqueada. Conviene desactivar auto-reload
en la cuenta: el límite de esta aplicación no controla compras del proveedor.

## Implementación

- Integración en `_improve_official_cotizacion_images`: solo imágenes importadas
  o de Quotation, principales y complementos; conserva fuentes y catálogos.
- OpenAI: misma configuración del piloto, Sunburst 2.5 fijado a 2026-09-08,
  PNG 1024×1024, high. SeedVR2: ruido 0.1, semilla fija, lado mayor 1024.
- Imágenes que ya tienen un lado >=1024 conservan sus bytes sin cargo nuevo.
- Biblioteca privada por usuario autenticado del job, compartida entre sus
  proyectos. Hash de bytes originales y parámetros; nunca incluye secretos.
- Selección automática persistente del resultado del proveedor elegido. Es
  selección operativa, no certificación de fidelidad ni aprobación visual.
  Una imagen OpenAI ya seleccionada continúa reutilizándose tras el cambio.
- Presupuesto único entre todas las cuentas que usan las claves del worker.
  Importes en microdólares enteros. Reserva atómica US$0.10 antes de cada
  edición, reemplazada por el costo calculado del uso reportado al finalizar.
  Tarifa consultada 24-09-2026: imagen entrada 8 USD/M, texto 5 USD/M, salida
  30 USD/M. Entradas limitadas a menos de 1024 por lado; salida fija high.
- Sin uso completo reportado, o ante una interrupción ambigua, conserva la
  reserva. No repite automáticamente solicitudes de resultado desconocido.
- Cambia persistentemente a SeedVR2 si no cabe una reserva adicional o llega
  `credit_balance_exhausted`, `billing_hard_limit_reached`, `insufficient_quota`.
  El último también puede indicar cuota organizativa: no asegura saldo cero.
- No interpreta 429 por frecuencia, errores de red o credencial vencida como
  saldo agotado. Esos fallos son visibles; revisar antes de reintentar.
- No se regenera el mismo contenido al volver a cotizar. Cola fal persistida
  y recuperable, sin segundo POST cuando ya existe una solicitud en cola.

## Activación pendiente de publicación autorizada

El código está apagado por defecto. Configuración privada del worker:

```dotenv
QUOTATION_IMAGE_POLICY=openai_then_seedvr2
QUOTATION_IMAGE_LIBRARY_DIR=/var/lib/mobiliti-images
QUOTATION_OPENAI_BUDGET_USD=30
QUOTATION_OPENAI_PREVIOUS_USD=0.40
# OPENAI_API_KEY y FAL_KEY se instalan por canal seguro, nunca en frontend/Git.
```

US$0.40 es una provisión conservadora para los US$0.37849 estimados de las siete
ediciones del piloto; no es un cargo conciliado. Conciliar el saldo y cualquier
otro uso de la cuenta antes de activar. La aplicación no lee el saldo bancario
ni contabiliza llamadas ajenas a este worker. El corte reserva un margen y puede
dejar algunos centavos; no promete agotar exactamente el saldo.

Los dos importes se fijan al crear `presupuesto.db`. Cambiar variables o reiniciar
no reinicia el gasto ni el proveedor. No borrar ni recrear esa base para cambiar
el límite. Si falta una base previamente usada, restaurar respaldo antes de
activar; una base nueva constituye un presupuesto nuevo.

El volumen `mobiliti-image-library` persiste fuera del contenedor, separado de
temporales y propiedad de UID/GID 10001. Respaldar el directorio completo con el
worker detenido o usando backup SQLite. No funciona como presupuesto común entre
servidores distintos: migrar a una DB compartida antes de desplegar otro host.

La clave OpenAI creada para el piloto tiene expiración de siete días. Antes de
activación prolongada, asegurar una credencial vigente; su caducidad no dispara
el cambio por presupuesto. No modificar recargas ni crear claves automáticamente.

La biblioteca anterior del piloto se conserva intacta. Si se desea precargarla,
identificar primero su cuenta propietaria y copiar mediante backup SQLite al
directorio de esa cuenta; no compartir fotos entre usuarios arbitrariamente.

## Validación

Pruebas locales con proveedores simulados: prioridad, cache, aislamiento,
concurrencia del presupuesto, saldo agotado, persistencia del cambio, JPEG,
errores temporales y composición de imágenes importadas sin tocar catálogos.
No consumen crédito real. Espejo Vercel debe pasar prueba de paridad.
Resultados: 43 pruebas de política/biblioteca/compositor y 109 de worker/paridad
aprobadas, más las comprobaciones añadidas de identidad autenticada y generación
de ambos perfiles. La prueba de generación atraviesa un cambio OpenAI→SeedVR2
simulado, preserva las imágenes originales de Quotation y reutiliza los resultados
al generar el segundo perfil sin nuevas solicitudes. La nueva lógica de rechazo
y presupuesto también pasó su regresión focalizada.

Los dos Excel se abrieron, recalcularon, guardaron como copias y reabrieron con
Microsoft Excel sin recovery logs, alteración de fórmulas comerciales ni errores
REF/VALUE en las celdas comprobadas. Evidencia preservada en
`output/politica-imagenes-20260924/validacion-excel.json` y los XLSX adyacentes.
No se ejecutó una compilación Docker local porque Docker no está instalado en
este entorno. Pendiente: publicación, instalación segura de claves/configuración,
conciliación del saldo y E2E productivo.

Fuentes: [OpenAI prepago](https://help.openai.com/en/articles/8264644-setting-up-and-managing-prepaid-api-billing),
[modelo y tarifas](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst),
[SeedVR2 API](https://fal.ai/models/fal-ai/seedvr/upscale/image/api).
