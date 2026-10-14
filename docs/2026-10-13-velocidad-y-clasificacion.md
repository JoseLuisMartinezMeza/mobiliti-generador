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
| `QUOTATION_IMAGE_POLICY` | `off` | En producción, `seedvr2` desde el 13 de octubre |
| `QUOTATION_IMAGE_CONCURRENCY` | 6 | Fotos que se piden a la vez |
| `QUOTATION_IMAGE_DEADLINE_SECONDS` | 60 | Segundos durante los que se piden fotos nuevas |
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

## Pendiente

- Confirmar con la primera cotización real: el tiempo que muestra la web y, en el registro del worker, las líneas `quotation_images` y `visual_classification`.
- Si se quiere el tiempo exacto de BUCE PISO 8, regenerarla desde la web; sus fotos ya están en la biblioteca, así que medirá el armado y la clasificación, no SeedVR2.
