# Cotizaciones grandes en menos de tres minutos y clasificación de productos importados

13 de octubre de 2026. El usuario reportó dos cosas: una cotización tardó quince minutos, y un escritorio («lido privados ch») salió descrito como multicontacto. Pidió que una cotización similar tarde como máximo tres minutos y que el tipo de producto acierte casi siempre, de ser necesario con un modelo que vea la foto.

## Qué pasaba

### Tiempo

Datos del worker productivo (bitácora y biblioteca de imágenes):

| Cotización | Líneas | Fotos nuevas | Total | En fotos | Resto |
|---|---|---|---|---|---|
| `200-00045` BUCE PISO 8 | 49 | 27 | 15 min 20 s | 13 min 20 s | 2 min |
| `200-00044` BUCE PISO 11 | 27 | 19 | 11 min 31 s | 9 min 40 s | 1 min 50 s |

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

- **En paralelo.** Hasta seis fotos a la vez (`QUOTATION_IMAGE_CONCURRENCY`). Cada imagen distinta se pide una sola vez aunque esté en varias líneas.
- **Tope de tiempo.** Pasados 60 segundos (`QUOTATION_IMAGE_DEADLINE_SECONDS`) no se piden fotos nuevas; las que ya estaban en curso se esperan hasta 45 segundos más. Lo que ya está en la biblioteca se entrega siempre, aunque el tope haya pasado.
- **Una foto no tumba la cotización.** La que falla o no alcanza usa la foto original. El worker registra una línea `quotation_images` con cuántas se mejoraron, cuántas se saltaron por tiempo, cuántas fallaron y por qué. Antes cualquier error del proveedor dejaba la cotización en error. Lo que afectaría a todas las fotos sí la sigue tumbando, y se comprueba una vez antes de empezar: cuenta vacía, carpeta de la biblioteca que no se puede escribir, presupuesto de OpenAI sin configurar o clave ausente del proveedor en turno.
- **Límite de frecuencia.** Un HTTP 429 que no es de saldo no generó ni cobró nada: ahora se espera (8 y 20 segundos, sin pasar del tope) y se reintenta, y si persiste la foto queda reenviable. Antes quedaba `rechazada` hasta una corrección manual, y un 429 al consultar la cola de SeedVR2 tiraba una solicitud que seguía viva. Un tiempo de espera agotado o un error 5xx siguen sin reenviarse solos, porque el resultado es desconocido y podría cobrarse dos veces.

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

Armado de una cotización real de 40 líneas, en la misma máquina, con las fotos simuladas como si ya estuvieran en la biblioteca:

| Versión | Segundos |
|---|---|
| Desplegada (`45bcb4aa`) | 25.9 |
| Con los cambios | 12.9 |

La salida es idéntica parte por parte (597 partes) mientras la clasificación con Claude está apagada. La memoria máxima del proceso sube de 279 a unos 340 MB.

Con cada foto tardando 7 segundos (como SeedVR2), 30 fotos distintas pasan de 210 segundos en serie a 35.

Clasificación, sobre los mismos 76 productos:

| Método | Aciertos |
|---|---|
| Diccionario anterior | 60 (79 %) |
| Con la regla de bench | 66 (87 %) |
| Más Claude para los 18 de nombre no reconocido | 76 (100 %), en cinco corridas |

Cada consulta tardó entre 9 y 10 segundos y costó unos USD 0.002.

En la cotización de 40 líneas cambiaron diez categorías, las diez correctas: dos bench y cuatro escritorios `DU82` a `Escritorios-WorkStation`, `DU72` y `DU73` y «MESAS ONE TO ONE» a `Mesas de Juntas`, y «MESA ALTA COFEE BAR» a `Mesas de Apoyo`.

## Tiempo esperado en producción

Estimaciones para una cotización como BUCE PISO 8, no medidas en el servidor: suponen que el armado baja allá en la misma proporción que en la máquina de pruebas.

| Caso | Antes | Después |
|---|---|---|
| Fotos nuevas con OpenAI | 15 min 20 s | unos 2 min 15 s, con unas 12 fotos mejoradas y el resto original |
| Fotos nuevas con SeedVR2 | sin dato | unos 2 min, todas mejoradas |
| Fotos ya en la biblioteca | unos 2 min | unos 1 min 15 s |

Con OpenAI no se pueden mejorar 27 fotos nuevas en tres minutos: cada una tarda 30 segundos y la cuenta tiene un límite de solicitudes por minuto que no se conoce. SeedVR2 tardó 6.5 segundos por foto en el piloto y cuesta USD 0.001 por megapíxel, unas sesenta veces menos.

## Configuración

| Variable | Por defecto | Efecto |
|---|---|---|
| `QUOTATION_IMAGE_CONCURRENCY` | 6 | Fotos que se piden a la vez |
| `QUOTATION_IMAGE_DEADLINE_SECONDS` | 60 | Segundos durante los que se piden fotos nuevas |
| `QUOTE_LLM_FALLBACK_MODEL` | vacío | `haiku` o `sonnet`; además del respaldo de importación, activa la clasificación con foto |

Un valor inválido en las dos primeras usa el valor por defecto.

## Límites

- La regla «el diccionario manda si reconoce el nombre» deja pasar un nombre reconocido por un término ambiguo. El caso conocido era «bench» y ya tiene regla; otros se corrigen en el diccionario.
- La clasificación con Claude no se guarda: se vuelve a consultar en cada generación. En cinco corridas dio lo mismo.
- Si una llamada a OpenAI se cuelga más allá del tope, el proceso del trabajo entrega la cotización pero espera a esa llamada antes de cerrar (hasta su límite de 240 segundos), y el siguiente trabajo en cola espera con él. La consulta a la cola de SeedVR2 sí se corta 45 segundos después del tope y se reanuda en la siguiente cotización.
- Las tres cachés de fórmulas admiten hasta 131,072 entradas cada una. Una cotización de 40 líneas usa unas 17,000 fórmulas distintas, así que un tope menor perdería la mejora. El worker procesa cada trabajo en un proceso aparte, que libera esa memoria al terminar.

## Validación

- Suites rápidas (26 archivos: fórmulas, paquete, clasificación, fotos, worker, API, importador, descripciones y bundle): 894 aprobadas y una omitida.
- Suites pesadas del motor (19 archivos): 361 aprobadas, 10 omitidas y un fallo que ya existía, el constructor de la guía IDÉLIKA, que no se tocó. Dos pruebas más fallaban desde antes porque esperaban la variante `v18` de la plantilla oficial, que se firma como `v11` desde la actualización de SharePoint; se actualizaron.
- Salida idéntica parte por parte antes y después de cada optimización, con la clasificación con Claude apagada.
- Seis hilos contra la biblioteca SQLite real con un proveedor simulado: doce fotos, doce cargos, ninguna llamada repetida.
- Revisión de código en dos rondas sobre el rango `45bcb4aa..HEAD`. La primera encontró dos puntos de gravedad media (errores de configuración que quedaban ocultos y una regla de bench demasiado amplia) y cuatro menores; la segunda, que la comprobación de configuración estaba incompleta y que el plazo de la clasificación podía duplicarse. Todos se corrigieron con su prueba. La última corrección no pasó por una tercera revisión.

## Pendiente

- Desplegar worker y Vercel; requiere autorización del usuario. Nada de esto está en producción.
- Medir en el servidor una cotización real de tamaño parecido.
- Decidir si se cambia ya a SeedVR2 o se deja que el saldo de OpenAI se agote solo; el cambio automático al agotarse ya existía.
