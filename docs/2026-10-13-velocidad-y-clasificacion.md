# Cotizaciones grandes en menos de tres minutos y clasificación de productos importados

13 de octubre de 2026. El usuario reportó dos cosas: una cotización tardó quince minutos, y un escritorio («lido privados ch») salió descrito como multicontacto. Pidió que una cotización similar tarde como máximo tres minutos y que el tipo de producto acierte casi siempre, de ser necesario con un modelo que vea la foto.

## Qué pasaba

### Tiempo

Datos del worker productivo (bitácora y biblioteca de imágenes):

| Cotización | Líneas | Fotos nuevas | Total | En fotos | Resto |
|---|---|---|---|---|---|
| `200-00045` BUCE PISO 8 | 49 | 27 | 15 min 20 s | 13 min 20 s | 2 min |
| `200-00044` BUCE PISO 11 | 27 | 19 | 11 min 31 s | 9 min 40 s | 1 min 50 s |

Por etapa, según el registro del worker para BUCE PISO 8: preparar la entrada 14.8 s, armar el libro 901.5 s (fotos incluidas) y subirlo 4 s.

- Cada foto nueva de un producto importado se manda a OpenAI (`gpt-image-2.5-sunburst`, 1024 px, `high`) y tarda unos 30 segundos. Se pedían una por una.
- Cada foto cuesta USD 0.0615. La biblioteca registra 212 fotos y USD 12.70.
- Sin contar fotos, armar el libro de 49 líneas consume unos 100 segundos de procesador en el servidor.
- Las fotos que ya están en la biblioteca no se vuelven a pedir; por eso regenerar la misma cotización es rápido.

### Clasificación

La categoría de «lido privados ch» fue `Multicontactos`, y de ahí salió el texto «Power module model lido privados ch». El diccionario no reconoce ese nombre; cuando eso pasa decide la descripción, y gana el término más largo que aparezca: «cable management», que es un accesorio del escritorio. Los «BENCH LIDO PARA 2PX» quedaron en `Bancos` por la palabra «bench».

La categoría también fija la tarifa de instalación y el factor de volumen en `Mobiliti`, no solo el texto.

Medido en 76 productos importados reales de dos cotizaciones (BUCE PISO 8 y la de ESSENTIA con proveedor local), etiquetados a mano con nombre, descripción y foto: el diccionario acertaba 60 (79 %). Los 16 errores eran escritorios como multicontactos, mesas de juntas como gabinetes y estaciones de trabajo como bancos.

## Cambios

### Fotos

- **SeedVR2 en lugar de OpenAI.** Desde el despliegue, `QUOTATION_IMAGE_POLICY=seedvr2`: cada foto nueva tarda unos 6 segundos y cuesta unos USD 0.001. Las fotos que OpenAI ya mejoró se siguen usando desde la biblioteca.
- **En paralelo.** Hasta seis fotos a la vez (`QUOTATION_IMAGE_CONCURRENCY`). Cada imagen distinta se pide una sola vez aunque esté en varias líneas.
- **Tope de tiempo.** Pasados 60 segundos (`QUOTATION_IMAGE_DEADLINE_SECONDS`) no se piden fotos nuevas; las que ya estaban en curso se esperan hasta 45 segundos más. Lo que ya está en la biblioteca se entrega siempre, aunque el tope haya pasado.
- **Una foto no tumba la cotización.** La que falla o no alcanza usa la foto original. El worker registra una línea `quotation_images` con cuántas se mejoraron, cuántas se saltaron por tiempo, cuántas fallaron y por qué. Antes cualquier error del proveedor dejaba la cotización en error. Lo que afectaría a todas las fotos sí la sigue tumbando, y se comprueba una vez antes de empezar: cuenta vacía, carpeta de la biblioteca que no se puede escribir, presupuesto de OpenAI sin configurar o clave ausente del proveedor en turno.
- **Límite de frecuencia.** Un HTTP 429 que no es de saldo no generó ni cobró nada: ahora se espera (8 y 20 segundos, sin pasar del tope) y se reintenta, y si persiste la foto queda reenviable. Antes quedaba `rechazada` hasta una corrección manual, y un 429 al consultar la cola de SeedVR2 tiraba una solicitud que seguía viva. Un tiempo de espera agotado o un error 5xx siguen sin reenviarse solos, porque el resultado es desconocido y podría cobrarse dos veces.
- **Fotos pequeñas.** fal responde HTTP 422 si un lado mide menos de 128 px («Both dimensions must be at least 128 pixels») o si el factor de aumento sale de 1 a 10. La primera prueba en el servidor dejó así 3 de 30 fotos con la original. Ahora esas fotos se agrandan a 128 px antes de enviarlas; la selección se sigue guardando con la huella de la foto original.

Las fotos que no alcanzaron quedan pendientes para la siguiente generación de esa cotización: cada vez se piden las que falten.

### Armado

Sin cambiar un solo byte de la salida:

- Las fórmulas se tokenizaban unas diez veces cada una y `Mobiliti` se construía dos veces con las mismas fórmulas. Ahora los tokens y el traslado de rangos se guardan.
- Resolver una hoja analizaba el workbook y las catorce hojas en cada consulta, y los content types se analizaban quinientas veces. Ahora se guardan mientras las partes sean exactamente las mismas.
- Los 600 términos del diccionario se compilaban por cada texto clasificado. Ahora se compilan una vez.

### Clasificación

- **Regla nueva:** un «bench» con puestos en la jerga del proveedor («2PX», «P/8PX», «4 pax») es `Escritorios-WorkStation`. Un «bench» a secas puede ser banca o estación de trabajo: lo decide la foto, y sin Claude sigue en `Bancos`.
- **Claude con la foto.** Con `QUOTE_LLM_FALLBACK_MODEL` activo, los productos importados cuyo nombre el diccionario no reconoce se consultan en una sola llamada (Haiku, esfuerzo alto) con nombre, descripción, medidas y una miniatura de 512 px. La respuesta solo puede ser una de las trece categorías.
- **El diccionario sigue mandando cuando reconoce el nombre.** Así no cambian decisiones ya tomadas, como que una «Occasional Table» es mesa de apoyo. Los productos de catálogo no se consultan.
- Si Claude falla dos veces seguidas, la consulta completa pasa de 60 segundos o está apagado, se conserva la clasificación anterior. Queda una línea `visual_classification` en el worker con productos, tokens y segundos.

## Mediciones

### En la máquina de pruebas

Armado de una cotización real de 40 líneas con las fotos simuladas como si ya estuvieran en la biblioteca: 25.9 s con la versión desplegada (`45bcb4aa`) y 12.9 s con los cambios. Dentro de la imagen del worker (Linux, Python 3.12, dos CPU), 27.6 y 14.9 segundos.

La salida es idéntica parte por parte (597 partes) mientras la clasificación con Claude está apagada. La memoria máxima del proceso sube de 279 a unos 340 MB.

Clasificación, sobre los mismos 76 productos:

| Método | Aciertos |
|---|---|
| Diccionario anterior | 60 (79 %) |
| Con la regla de bench | 66 (87 %) |
| Más Claude para los 18 de nombre no reconocido | 76 (100 %), en cinco corridas |

En la cotización de 40 líneas cambiaron diez categorías, las diez correctas: dos bench y cuatro escritorios `DU82` a `Escritorios-WorkStation`, `DU72` y `DU73` y «MESAS ONE TO ONE» a `Mesas de Juntas`, y «MESA ALTA COFEE BAR» a `Mesas de Apoyo`.

### En el servidor de producción

La misma cotización de 40 líneas, en contenedores desechables de la imagen del worker (sin datos ni biblioteca de producción):

| Caso | Antes (`6122b56f`) | Después (`6e9eff47`) |
|---|---|---|
| Armado con las fotos ya en la biblioteca | 18.7 s | 10.8 s |
| Con 30 fotos nuevas por SeedVR2 y Haiku | sin dato | 41.7 s |

Detalle de la corrida con proveedores reales: fotos 25.1 s, sin fallos (24 por SeedVR2, entre 5.9 y 6.6 segundos cada una y seis a la vez, y 6 que ya medían 1024 px o más); Haiku 6.6 s (13 productos, 8,614 tokens de entrada y 1,166 de salida). Las corridas simuladas son medianas de tres, que no variaron más de 0.1 s.

### Tiempo esperado para BUCE PISO 8

No se pudo repetir esa cotización: el worker borra la entrada de cada trabajo al terminarlo. Estimación con sus etapas medidas:

| Etapa | Antes | Ahora |
|---|---|---|
| Preparar la entrada | 14.8 s | igual |
| 27 fotos nuevas | unos 800 s | unos 30 s (cinco tandas de seis) |
| Clasificación con Haiku | no existía | unos 7 s |
| Resto del armado | unos 100 s | entre 60 y 100 s |
| Subir el resultado | 4 s | igual |
| **Total** | **15 min 20 s** | **entre 2 y 2 min 40 s** |

El caso extremo, con fal y Claude agotando sus topes (105 y 60 segundos), llegaría a unos 4 min 45 s. Regenerar una cotización cuyas fotos ya están en la biblioteca debería quedar entre 1 min 30 s y 2 min 10 s.

## Costos

- SeedVR2: USD 0.001 por megapíxel de salida (tarifa de fal consultada el 13 de octubre); una foto de 1024 px cuesta unos USD 0.001, contra USD 0.0615 con OpenAI.
- Haiku 5.5: USD 0.10 y 0.50 por millón de tokens de entrada y salida; la consulta de 13 productos costó unos USD 0.0014.
- Las pruebas en el servidor del 13 de octubre usaron unas 66 fotos de SeedVR2 y tres consultas a Haiku: unos USD 0.07.

## Configuración

| Variable | Por defecto | Efecto |
|---|---|---|
| `QUOTATION_IMAGE_POLICY` | `off` | En producción, `seedvr2` el 13 de octubre y `openai_then_seedvr2` (OpenAI `medium`) desde el 14 |
| `QUOTATION_IMAGE_CONCURRENCY` | 6 | Fotos que se piden a la vez |
| `QUOTATION_IMAGE_DEADLINE_SECONDS` | 60 | Segundos durante los que se piden fotos nuevas; en producción 100 desde el 14 de octubre |
| `QUOTE_LLM_FALLBACK_MODEL` | vacío | `haiku` o `sonnet`; además del respaldo de importación, activa la clasificación con foto |

Un valor inválido en `QUOTATION_IMAGE_CONCURRENCY` o `QUOTATION_IMAGE_DEADLINE_SECONDS` usa el valor por defecto.

## Límites

- La regla «el diccionario manda si reconoce el nombre» deja pasar un nombre reconocido por un término ambiguo. El caso conocido era «bench» y ya tiene regla; otros se corrigen en el diccionario.
- La clasificación con Claude no se guarda: se vuelve a consultar en cada generación. En cinco corridas dio lo mismo.
- Si una llamada a un proveedor se cuelga más allá del tope, el proceso del trabajo entrega la cotización pero espera a esa llamada antes de cerrar (hasta su límite de 240 segundos), y el siguiente trabajo en cola espera con él. La consulta a la cola de SeedVR2 sí se corta 45 segundos después del tope y se reanuda en la siguiente cotización.
- Una foto de menos de 128 px se agranda antes de SeedVR2; el resultado es más limpio que la original, pero no recupera detalle que la foto no tiene.
- Las tres cachés de fórmulas admiten hasta 131,072 entradas cada una. Una cotización de 40 líneas usa unas 17,000 fórmulas distintas, así que un tope menor perdería la mejora. El worker procesa cada trabajo en un proceso aparte, que libera esa memoria al terminar.

## Validación

- Suites rápidas (26 archivos: fórmulas, paquete, clasificación, fotos, worker, API, importador, descripciones y bundle): 894 aprobadas y una omitida.
- Suites pesadas del motor (19 archivos): 361 aprobadas, 10 omitidas y un fallo que ya existía, el constructor de la guía IDÉLIKA, que no se tocó. Dos pruebas más fallaban desde antes porque esperaban la variante `v18` de la plantilla oficial, que se firma como `v11` desde la actualización de SharePoint; se actualizaron.
- Tras la corrección de fotos pequeñas: política de fotos, paridad del bundle, motor de Proyectos y clasificación con foto, 72 aprobadas.
- Salida idéntica parte por parte antes y después de cada optimización, con la clasificación con Claude apagada.
- Seis hilos contra la biblioteca SQLite real con un proveedor simulado: doce fotos, doce cargos, ninguna llamada repetida.
- Revisión de código en dos rondas desde `45bcb4aa`. La primera encontró dos puntos de gravedad media (errores de configuración que quedaban ocultos y una regla de bench demasiado amplia) y cuatro menores; la segunda, que la comprobación de configuración estaba incompleta y que el plazo de la clasificación podía duplicarse. Todos se corrigieron con su prueba. Ni esa última corrección ni la de fotos pequeñas pasaron por otra revisión; la de fotos pequeñas se comprobó con fal real en el servidor.

## Despliegue

13 de octubre de 2026 (02:58 a 03:10 UTC del 14), con autorización del usuario. Solo el worker; Vercel no cambia, porque la API solo usa del motor el tipo de cambio.

- Rama `codex/velocidad-y-clasificacion-20261013`, entregada por bundle en tres pasos: `0912c862` (los cambios), `7365dd27` (tope del factor de SeedVR2, que no bastó) y `6e9eff47` (fotos pequeñas). `CURRENT=6e9eff47`, contenedor sano y sin reinicios.
- `/etc/mobiliti-worker/worker.env`: `QUOTATION_IMAGE_POLICY` pasó de `openai_then_seedvr2` a `seedvr2`. Respaldo: `worker.env.bak-20261013-antes-seedvr2`. La clave de OpenAI sigue en el archivo, sin uso.
- Contenedor previo a estos cambios: `mobiliti-worker-backup-20261014025846-ba4fe80789ab` (imagen `6122b56f`, con su propia política `openai_then_seedvr2`). Para volver a él: detener y renombrar `mobiliti-worker`, renombrar ese respaldo a `mobiliti-worker`, arrancarlo y escribir `6122b56f9a892b9e0536e0ab47a941115aa7a6bd` en `/opt/mobiliti-worker/releases/CURRENT`. Restaurar también el respaldo de `worker.env`, para que el siguiente despliegue no vuelva a SeedVR2.

## Fotos con OpenAI medium (14 de octubre)

Tras comparar diez modelos y SeedVR2 con fotos reales (`output/comparativa-ia-20261013/`), el usuario eligió el acabado de OpenAI: reconstruye cubiertas pixeladas y con reflejo, quita fondos o pisos grises y respeta el producto. Ninguna alternativa cumplió las tres cosas. SeedVR2 conserva el reflejo y el piso gris.

- `gpt-image-2.5-sunburst` en calidad `medium` (en la comparación se ve como `high`) y fondo opaco, con una instrucción nueva: reconstruir superficies, quitar fondos o pisos grises y dejar el producto al 92 % del cuadro.
- A OpenAI se envía el producto centrado según su transparencia, sobre blanco y a 512 px. Una foto sin transparencia se envía tal cual. La selección se sigue guardando con la huella de la foto original.
- Toda foto entregada, de OpenAI, de SeedVR2 o ya guardada, se recorta al producto con un margen de 3 %; nunca se agregan píxeles. Las fotos que SeedVR2 mejoró el 13 de octubre se conservan.

Medición en el servidor, en un contenedor desechable con biblioteca temporal: BUCE PISO 8 (49 líneas, 34 fotos nuevas; en producción eran 27) por `generate_quote`, con el tope todavía en 60 s.

| | Primera generación | Regenerar |
|---|---|---|
| Fotos | 69.4 s; 24 de 34 mejoradas, 10 por tope | 35.9 s; 34 de 34 |
| Armado completo | 163.9 s | 130.1 s |
| Con preparar entrada (14.8 s) y subir (4 s) de la bitácora | unos 3 min 3 s | unos 2 min 29 s |

- Costo real: USD 0.0225 por foto (promedio de 944 tokens de imagen de entrada, 325 de texto y 439 de salida), 16 s por foto. Las 34 fotos costaron USD 0.77; las 27 de producción serían unos USD 0.61, contra USD 1.66 con `high`.
- Haiku: 3 s y unos 5,400 tokens de entrada por corrida.
- La copia de la entrada (`fuente_buce8.xlsx`) tiene 8 estilos con `xfId` fuera de rango, que el motor rechaza al copiar la Quotation; se repararon solo para medir. El primer intento falló por eso después de generar 24 fotos. Gasto total de la medición: unos USD 1.30.
- Con el tope en 100 s se espera que las fotos de una cotización así salgan todas en la primera generación, en unos 3.5 minutos.

Validación: política de fotos, paridad del bundle, motor de Proyectos, piloto de fotos, worker y clasificación con foto, 181 aprobadas. Las cuatro fotos de la comparación producen con este código la misma entrada que se comparó (diferencia de 0 píxeles).

Despliegue el 14 de octubre (04:14 UTC), con autorización del usuario. Solo el worker.

- Commit `50cd453e`, entregado por bundle desde `6e9eff47`. `CURRENT=50cd453e`, contenedor sano.
- `/etc/mobiliti-worker/worker.env`: `QUOTATION_IMAGE_POLICY` de `seedvr2` a `openai_then_seedvr2` y `QUOTATION_IMAGE_DEADLINE_SECONDS=100`. Respaldo: `worker.env.bak-20261014-antes-openai-medium`.
- Contenedor previo: `mobiliti-worker-backup-20261014041407-1100bfcf8570` (imagen `6e9eff47`, con SeedVR2). Para volver a él, el mismo procedimiento de arriba con `6e9eff472af3f73169b090cbc778ff6743937375` en `CURRENT` y el respaldo de `worker.env` de este despliegue.
- Presupuesto de la app al desplegar: USD 16.90 disponibles de 30. El saldo real de la cuenta de OpenAI puede ser menor; al agotarse, el worker pasa solo a SeedVR2.

## Estilos con `xfId` fuera de rango (14 de octubre)

La entrada de BUCE PISO 8 tiene 14 estilos de celda (`cellXfs`) cuyo `xfId` (116 a 123) supera los 116 estilos con nombre (`cellStyleXfs`). Excel los abre como el estilo Normal (0); el motor lanzaba `ValueError: Referencia xfId fuera de rango` al copiar la Quotation y la cotización no se generaba. Ahora el remapeo de estilos los trata como 0. Si el archivo no tiene ningún estilo con nombre, sigue fallando.

- Prueba nueva en `tests/test_quotation_sheet_transplant.py`. Con la copia del motor web sincronizada: trasplante y paridad del bundle, 137 aprobadas.
- Las 16 suites del motor que usan `quotation_sheets`: 330 aprobadas, 1 omitida, la falla ya conocida del constructor IDÉLIKA y 6 errores de `test_template_variant_e2e.py` porque falta su archivo real de entrada en este worktree.
- No es la causa de la falla de ESSENTIA por «Nueva cotización»: sus dos Quotations de fábrica no tienen `xfId` fuera de rango y el error registrado es otro («Más de una imagen Quotation para la misma fila»).

Despliegue el 14 de octubre (04:56 UTC), con autorización del usuario. Solo el worker; Vercel no ejecuta este código.

- Commit `9051d17c`, entregado por bundle desde `50cd453e`. `CURRENT=9051d17c`, sin cambios en `worker.env`. Sano y sin reinicios de 04:57 a 05:07 UTC.
- Con la imagen productiva, en un contenedor desechable sin fotos ni Claude, BUCE PISO 8 se generó desde la entrada sin reparar en 188 s.
- Contenedor previo: `mobiliti-worker-backup-20261014045644-55d697d16787` (imagen `50cd453e`). Para volver a él, el mismo procedimiento de arriba con `50cd453ec63af5519bf3f4ea87f47a86d30ad24e` en `CURRENT`; no hay respaldo de `worker.env` porque no cambió.
- Antes del despliegue, los contenedores `50cd453e` (desde unas 04:17 UTC) y `6e9eff47` (desde unas 04:09) estaban `unhealthy`: `/health` respondía 503 con `worker_cycle_failed` y `processed: 0`, sin trabajos en la cola ni trazas en el registro. El ciclo del worker descarta la excepción y ese estado solo se limpia al procesar un trabajo; la salud que se mide al desplegar es anterior al primer ciclo. La causa no se pudo saber.

## Pendiente

- Confirmar con la primera cotización real: el tiempo que muestra la web y, en el registro del worker, las líneas `quotation_images` y `visual_classification`.
- Si se quiere el tiempo exacto de BUCE PISO 8, regenerarla desde la web; sus fotos ya están en la biblioteca, así que medirá el armado y la clasificación, no SeedVR2.
- Registrar la excepción del ciclo del worker y limpiar `worker_cycle_failed` tras un ciclo en reposo sin errores, para que una falla pasajera no deje el worker `unhealthy` sin rastro.
