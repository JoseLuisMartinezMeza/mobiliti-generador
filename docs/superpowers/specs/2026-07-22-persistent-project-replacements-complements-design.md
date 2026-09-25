# Proyectos persistentes, sustituciones y complementos — diseño aprobado

Fecha: 2026-07-22
Proyecto: Mobiliti SaaS Cotizador
Estado: aprobado para planificación, no implementado
Producción y SharePoint: sin cambios

## 1. Resumen

El concepto visible de **Carrito** se reemplazará por **Proyecto**. Un Proyecto será un
borrador persistente, privado por usuario y editable desde un módulo propio. Guardará
datos comerciales, secciones, ocurrencias ordenadas, líneas importadas y complementos.

Cada ocurrencia podrá:

1. sustituirse individualmente;
2. sustituirse junto con todas las ocurrencias del mismo proveedor y código oficial;
3. recibir uno o varios complementos de un solo nivel.

La misma composición tendrá dos proyecciones:

- `Mobiliti`: principal y complementos como filas normales separadas;
- `Cotizacion`: una sola línea comercial con precio agregado, descripciones prefijadas
  con `+` y una imagen compuesta con miniaturas.

El Proyecto será una entidad distinta de los trabajos del generador. Generar una
cotización creará una instantánea inmutable de una revisión concreta y no vaciará ni
consumirá el Proyecto.

## 2. Decisiones aprobadas

| Tema | Decisión |
|---|---|
| Persistencia | Servidor, privada por usuario |
| Entidad | `Proyecto` dedicado; no reutilizar `quote_jobs` |
| Guardado | Automático con revisión optimista |
| Eliminación | Archivado recuperable en la primera entrega |
| Ocurrencias repetidas | Independientes, con `line_id` propio |
| Reemplazar todos | Proveedor/catálogo normalizado + código oficial |
| Importados | Participan directamente cuando tienen proveedor y código |
| Cantidad de complemento | Elegible: por unidad o fija |
| Profundidad | Un solo nivel; no hay complementos de complementos |
| Reemplazo de principal | Retira automáticamente sus complementos |
| Cotización exitosa | El Proyecto permanece guardado |
| Despliegue | Primero localhost; producción requiere otra aprobación |

## 3. Objetivos

- Renombrar de manera coherente el concepto visible y accesible de Carrito a Proyecto.
- Permitir guardar, cerrar, reabrir y continuar un Proyecto desde otra sesión.
- Mantener la edición manual de secciones, conceptos, orden y cantidades.
- Permitir varias ocurrencias del mismo producto en secciones distintas.
- Reutilizar un selector autoritativo para agregar, sustituir y complementar.
- Hacer participar a líneas importadas en las sustituciones masivas.
- Mantener los costos y fórmulas oficiales de `Mobiliti`.
- Generar una composición correcta en `Cotizacion` sin duplicar conversión ni descuento.
- Producir archivos XLSX válidos que Excel abra sin reparación.
- Conservar la capacidad dinámica para Proyectos grandes.

## 4. Fuera de alcance

- Compartir o coeditar Proyectos entre usuarios.
- Trabajo fuera de línea o sincronización mediante `localStorage`.
- Anidamiento de complementos de más de un nivel.
- Inferir códigos oficiales desde nombres de producto mediante heurísticas.
- Crear un segundo motor de catálogos, precios o cotizaciones.
- Modificar o publicar documentos de SharePoint.
- Desplegar a producción durante esta entrega.

## 5. Contexto y restricciones existentes

La aplicación ya tiene:

- una lista global de líneas mixtas;
- secciones manuales, orden, movimiento y conceptos editables;
- importación de Quotation al editor global;
- identidades canónicas por catálogo;
- un constructor mixto que conserva `groups` financieros y `sections` de presentación;
- un compositor OOXML que preserva la plantilla oficial;
- capacidad dinámica de filas y secciones;
- resolución autoritativa de precios, monedas, disponibilidad e imágenes.

La implementación extenderá esas piezas. No se creará otro editor ni otro generador.

Las especificaciones que permanecen vigentes son:

- `2026-07-19-mixed-catalog-cart-design.md`;
- `2026-07-20-manual-cart-sections-design.md`;
- `2026-07-21-imported-quotation-global-cart-design.md`;
- `2026-07-21-official-template-preservation-and-dynamic-capacity-design.md`.

Cuando exista una contradicción sobre identidad visual o persistencia, este documento
reemplaza únicamente las reglas antiguas del Carrito.

## 6. Modelo de dominio

### 6.1 Proyecto

La entidad persistente tendrá como mínimo:

```text
id
usuario_id
nombre
estado                 active | archived
revision               entero monotónico
schema_version
payload                JSON validado
created_at
updated_at
archived_at
```

`usuario_id` es obligatorio y participa en toda consulta. Un administrador no obtiene
acceso implícito a Proyectos ajenos en esta primera versión.

### 6.2 Payload

El payload del Proyecto contiene:

```json
{
  "schema_version": 1,
  "quote_fields": {
    "proyecto": "Oficinas Mobiliti",
    "cliente": "Cliente",
    "correo": "cliente@example.com",
    "telefono": "33 0000 0000",
    "direccion": "Guadalajara",
    "razon_social": "Cliente SA de CV",
    "quote_currency": "MXN",
    "descuento": "40"
  },
  "sections": [
    {
      "section_id": "section-1",
      "concept": "Recepción",
      "position": 0
    }
  ],
  "lines": []
}
```

La estructura almacenada tendrá una sola fuente de orden:

- la sección tiene `position`;
- cada principal tiene `section_id` y `position`;
- cada complemento tiene `parent_line_id` y `position`.

No se mantendrán simultáneamente arreglos duplicados de IDs y posiciones.

### 6.3 Ocurrencia

Cada aparición tendrá un `line_id` UUID estable e independiente de su identidad
comercial.

Campos comunes:

```text
line_id
role                   principal | complement
section_id              sólo principal
parent_line_id          sólo complement
position
quantity
quantity_rules_cache    sólo visual, no autoritativo
display_cache           nombre/código/imagen para render inmediato
```

`display_cache` nunca será fuente de precio, moneda, inventario ni imagen de salida.
Al abrir y cotizar, el servidor vuelve a resolver las referencias canónicas.

### 6.4 Referencia de producto de catálogo

Una línea de catálogo conserva el contrato vigente:

- Tarkett: `catalog + code`;
- Offiho: `catalog + inventory_key`;
- proveedores generales: `catalog + internal_id + base_option_id +
  add_on_option_ids`.

Además se guarda el `official_code` normalizado usado para sustituciones masivas.
Este campo debe corresponder al valor resuelto por el servidor; el navegador no puede
inventarlo.

### 6.5 Línea importada

Una línea importada conserva:

```text
import_id
source_row
source_currency
official_code
provider
name
description
dimension
unit_price
image_asset_key
source_asset_key
```

El importador preservará el código de la columna fuente cuando exista. Si la Quotation
no tiene código o proveedor identificable, la interfaz permitirá capturarlos
explícitamente. No se extraerá el código desde el nombre.

Una línea importada sin proveedor y código válidos sigue siendo cotizable, pero no
participa silenciosamente en `Reemplazar todos`. La interfaz lo explica.

### 6.6 Complemento

Un complemento es una ocurrencia normal con:

```text
role = complement
parent_line_id
quantity_mode          per_parent_unit | fixed_project
quantity
```

Reglas:

- el padre debe existir y ser `principal`;
- un complemento no puede ser padre;
- no puede referenciarse a sí mismo;
- no puede formar ciclos;
- varios complementos pueden usar el mismo producto;
- un complemento no aparece directamente en el orden de secciones.

## 7. Persistencia y recursos

### 7.1 Base de datos

Se agregará una tabla `saas_projects` tanto en PostgreSQL/Supabase como en el almacén
local de desarrollo. Los tres espejos desplegables de la API mantendrán el mismo
contrato.

La escritura será atómica:

```sql
UPDATE saas_projects
SET payload = :payload,
    revision = revision + 1,
    updated_at = now()
WHERE id = :id
  AND usuario_id = :usuario_id
  AND revision = :expected_revision
RETURNING *;
```

Si no hay fila actualizada, la API devuelve conflicto y la versión vigente.

### 7.2 Recursos importados

Los archivos y las imágenes importadas que pertenezcan a un Proyecto no dependerán
del ciclo de retención de un `quote_job`.

Se copiarán o promoverán a rutas propias:

```text
projects/{usuario_id}/{project_id}/sources/...
projects/{usuario_id}/{project_id}/images/...
```

El payload guarda claves de objeto, nunca rutas locales ni URLs externas arbitrarias.
Archivar el Proyecto no retira sus recursos.

### 7.3 Instantánea de cotización

Al generar se crea una instantánea inmutable con:

```text
project_id
project_revision
normalized_project_payload
catalog source hashes/revisions
template contract hash
exchange-rate snapshot
```

El `quote_job` referencia esa instantánea. Cambios posteriores en el Proyecto no
alteran un trabajo ya encolado ni su archivo.

## 8. API

### 8.1 Proyectos

```text
POST   /projects
GET    /projects?status=active|archived
GET    /projects/{project_id}
PATCH  /projects/{project_id}
POST   /projects/{project_id}/archive
POST   /projects/{project_id}/restore
POST   /projects/{project_id}/duplicate
POST   /projects/{project_id}/quote
```

`PATCH` recibe `expected_revision`, un `operation_id` idempotente y el payload completo
validado. La respuesta devuelve la revisión confirmada.

No se incluirá `DELETE` en la primera entrega.

### 8.2 Búsqueda para selector

El selector reutilizará los cargadores de catálogo actuales mediante una consulta
agregada y paginada:

```text
GET /catalogs/search?q=...&supplier=...&cursor=...
```

La respuesta contiene referencias canónicas y snapshots visuales, no introduce otro
índice comercial. Precio, moneda y disponibilidad se resuelven por los servicios
vigentes.

### 8.3 Compatibilidad

`POST /catalogs/mixed-quote` seguirá disponible para clientes anteriores. El flujo
nuevo usará `/projects/{id}/quote` para que el servidor genere desde el Proyecto
persistido y no confíe en una copia enviada por el navegador.

## 9. Operaciones del editor

### 9.1 Agregar producto

Agregar siempre crea un nuevo `line_id`. El mismo producto puede aparecer en otra
sección o repetirse en la misma.

### 9.2 Reemplazar una ocurrencia

La operación:

1. recibe `line_id` y referencia canónica de destino;
2. resuelve el producto en servidor;
3. valida la cantidad contra las reglas nuevas;
4. conserva sección, posición y cantidad;
5. si es principal, retira todos sus complementos;
6. si es complemento, conserva modo y cantidad cuando sean válidos;
7. aplica todo o nada.

Antes de reemplazar un principal, la interfaz informa cuántos complementos se
retirarán.

### 9.3 Reemplazar todos

La coincidencia usa:

```text
normalized_provider_or_catalog + official_code
```

Incluye principales, complementos y líneas importadas de todas las secciones.

La vista previa informa:

- ocurrencias encontradas;
- líneas de catálogo;
- líneas importadas;
- secciones afectadas;
- complementos que serán retirados por reemplazar principales;
- líneas excluidas por falta de proveedor o código.

Todas las cantidades se validan primero. Si una falla, no se aplica ningún cambio.

### 9.4 Agregar complemento

El selector es el mismo de productos. Después de elegir:

1. se selecciona `Por unidad del principal` o `Cantidad fija del Proyecto`;
2. se captura una cantidad válida;
3. se muestra el impacto en unidades e importe;
4. se confirma y guarda como hijo directo.

## 10. Interfaz

### 10.1 Terminología

Todo texto visible, etiqueta accesible y mensaje nuevo usará Proyecto:

- `Carrito (N)` → `Proyecto (N)`;
- `Agregar` → `Agregar al Proyecto`;
- `Cotizar todos los catálogos` → `Generar cotización`;
- `Vaciar carrito` → no aplica; el Proyecto se archiva o edita.

Las variables internas antiguas pueden migrarse en pasos pequeños, pero ningún
contrato nuevo de API o dominio se llamará carrito.

### 10.2 Módulo Proyectos

El menú lateral añade `Proyectos`. La vista contiene:

- activos y archivados;
- nombre;
- última actualización;
- número de principales y complementos;
- acciones abrir, duplicar, archivar y restaurar.

No se añadirá eliminación permanente.

### 10.3 Editor

El editor tiene:

- nombre de Proyecto editable;
- indicador `Guardando`, `Guardado` o `Cambios pendientes`;
- pestaña `Productos`;
- pestaña `Datos de cotización`;
- secciones manuales y conceptos actuales;
- reordenamiento y movimiento entre secciones.

Cada principal muestra:

```text
Cambiar producto
Cambiar todos los iguales
Agregar complemento
```

Los complementos aparecen dentro de la tarjeta con miniatura, nombre, código, modo,
cantidad, cambiar y quitar.

### 10.4 Selector y previsualización

El selector:

- busca en los siete catálogos;
- filtra por proveedor;
- pagina resultados;
- muestra código, nombre, configuración, disponibilidad e imagen;
- presenta una imagen grande antes de confirmar;
- usa el mismo componente para agregar, sustituir y complementar.

Un producto sin imagen válida muestra `Sin imagen`. No se sustituye silenciosamente
por una imagen externa incierta.

### 10.5 Adaptación responsiva

Mientras se navega un catálogo, el botón superior abre un panel rápido del Proyecto.
La edición completa vive en el módulo propio. En viewports angostos el editor ocupa la
pantalla completa para evitar el panel estrecho actual.

## 11. Guardado automático y conflictos

Los cambios se agrupan con un debounce corto. Sólo una confirmación del servidor
actualiza el estado a `Guardado`.

En fallo de red:

- el estado queda `Cambios pendientes`;
- se conserva la edición en memoria;
- se reintenta con el mismo `operation_id`;
- no se muestra una confirmación falsa.

En conflicto de revisión:

- no se sobrescribe;
- se presenta la revisión vigente;
- el usuario puede recargar o duplicar su versión como otro Proyecto.

No se implementará mezcla automática de dos ediciones concurrentes.

## 12. Proyección a Excel

### 12.1 Resolución previa

Antes de encolar:

1. se valida el grafo principal–complementos;
2. se resuelven todas las referencias de catálogo;
3. se normalizan importados;
4. se captura una sola instantánea de tipos de cambio;
5. se valida capacidad física;
6. se congela la instantánea.

### 12.2 `Mobiliti`

Cada componente ocupa una fila independiente. El orden es:

```text
principal
complemento 1
complemento 2
siguiente principal
```

Todos permanecen en la sección del principal.

Cantidad:

- `per_parent_unit`: `principal.quantity × complement.quantity`;
- `fixed_project`: `complement.quantity`.

Cada fila conserva costo, proveedor, moneda e identidad propios. Los precios de
entrada son costos. Las fórmulas oficiales de `Mobiliti` aplican el aumento y reglas
comerciales; el compositor no reemplaza esa lógica.

### 12.3 `Cotizacion`

Sólo el principal crea una línea visible.

Descripción:

```text
Descripción del principal
+ Descripción del complemento 1
+ Descripción del complemento 2
```

El precio compuesto se deriva de las celdas calculadas de `Mobiliti`.

Para un principal con cantidad `Q`, precio unitario calculado `P`, complementos por
unidad `(Rᵢ, Cᵢ)` y complementos fijos `(Fⱼ, Cⱼ)`:

```text
precio_unitario_compuesto =
    P
    + Σ(Rᵢ × Cᵢ)
    + Σ(Fⱼ × Cⱼ) / Q
```

Así:

```text
Q × precio_unitario_compuesto =
    Q × P
    + Σ(Q × Rᵢ × Cᵢ)
    + Σ(Fⱼ × Cⱼ)
```

La fórmula se construye con referencias vivas a las filas correspondientes. Se usan
decimales exactos para validación y se conserva el redondeo oficial de la plantilla.

El descuento comercial se aplica una sola vez a la línea compuesta. La moneda se
convierte una sola vez por componente; no se reconvierte un resultado ya normalizado.

### 12.4 Imágenes

Para cada composición se genera un único montaje raster:

- imagen principal dominante;
- miniaturas de complementos;
- fondo blanco;
- proporción preservada;
- tamaño limitado antes de empacar.

Insertar un solo recurso por línea reduce relaciones OOXML y riesgo de corrupción.
La interfaz previsualiza la misma jerarquía visual.

### 12.5 Preservación de plantilla

- `Quotation` importada permanece intacta;
- fórmulas oficiales de `Mobiliti` se propagan, no se sustituyen;
- hojas ocultas, nombres, vínculos, dibujos y valores fijos quedan protegidos;
- sólo se modifican áreas dinámicas declaradas por el contrato oficial;
- el paquete final debe pasar validación ZIP/OOXML y apertura con Excel sin reparación.

## 13. Capacidad

No se reintroducirán límites artificiales de 16 secciones, 33 productos ni el límite
de 32 secciones de la UI heredada.

La capacidad se calcula con:

- filas de principales;
- filas de complementos;
- encabezados y subtotales de sección;
- filas reservadas por la plantilla;
- límite físico de hoja XLSX;
- límite de tamaño de solicitud.

La API devolverá capacidad restante y errores estructurados. Si el Proyecto supera el
límite físico, se rechaza antes de crear el trabajo. La expansión dinámica vigente
continúa siendo la única estrategia de crecimiento.

## 14. Seguridad

- Toda operación filtra por `usuario_id`.
- Se usan allowlists de campos y tamaños.
- `line_id`, `parent_line_id`, secciones y posiciones se validan.
- Se rechazan duplicados, huérfanos, ciclos y profundidad mayor a uno.
- Los textos pasan por neutralización de fórmulas de Excel.
- Los recursos se identifican por claves de almacenamiento controladas.
- Los catálogos siguen siendo autoridad de precio, moneda, disponibilidad e imagen.
- Los proyectos archivados son de sólo lectura hasta restaurarse.
- `operation_id` evita repetir un guardado o reemplazo después de un reintento.

## 15. Manejo de errores

| Situación | Comportamiento |
|---|---|
| Red durante autoguardado | Conservar en memoria, mostrar pendiente, reintentar |
| Revisión obsoleta | `409`, no sobrescribir, ofrecer recargar o duplicar |
| Producto ya no existe | Marcar línea y bloquear cotización hasta resolver |
| Precio por confirmar | Aplicar advertencia vigente y confirmación explícita |
| Imagen ausente | Mostrar `Sin imagen`; no inventar una referencia |
| Cantidad inválida al reemplazar | Cancelar toda la operación |
| Generación fallida | Conservar Proyecto y mostrar trabajo fallido |
| Capacidad excedida | Rechazar antes de encolar con conteos claros |
| XLSX inválido en postvalidación | Marcar trabajo fallido; no ofrecer descarga |

## 16. Pruebas

### 16.1 Dominio y frontend

- agregar el mismo producto crea dos `line_id`;
- sustituir uno no altera el otro;
- sustituir todos incluye catálogos e importados compatibles;
- exclusiones por código/proveedor se informan;
- reemplazar principal retira complementos;
- reemplazar complemento conserva modo y cantidad válidos;
- se rechaza anidamiento mayor a uno;
- los dos modos de cantidad producen unidades correctas;
- secciones y orden sobreviven a guardar/reabrir;
- no queda texto visible o accesible `Carrito`;
- selector reutilizado en los tres contextos;
- previsualización y miniaturas tienen estado `Sin imagen`.

### 16.2 API y persistencia

- aislamiento por usuario;
- CRUD sin eliminación permanente;
- revisión optimista y respuesta de conflicto;
- idempotencia de `operation_id`;
- escritura atómica del almacén local;
- paridad de los tres espejos de API;
- recursos importados sobreviven al ciclo del `quote_job`;
- archivado y restauración;
- instantánea de revisión inmutable.

### 16.3 Motor y OOXML

- principal y complementos separados en `Mobiliti`;
- composición única en `Cotizacion`;
- fórmulas correctas para cantidades por unidad y fijas;
- descuento aplicado una vez;
- conversión aplicada una vez;
- costos alimentan fórmulas oficiales;
- descripciones con `+`;
- montaje con principal y miniaturas;
- `Quotation` intacta;
- partes protegidas y hashes contractuales conservados;
- ausencia de referencias rotas y `#REF!`;
- paquete ZIP válido;
- apertura y guardado por Excel sin diálogo de reparación.

### 16.4 Estrés

- más de 16 secciones;
- más de 33 productos por sección;
- más de 16 × 33 componentes totales;
- mezcla de importados, catálogos y complementos;
- capacidad cercana al límite físico;
- error anticipado al exceder el límite.

## 17. Criterios de aceptación

1. Un usuario crea un Proyecto, cierra sesión, vuelve a entrar y continúa sin pérdida.
2. El mismo código puede existir en varias secciones como ocurrencias independientes.
3. `Cambiar producto` modifica sólo la ocurrencia elegida.
4. `Cambiar todos` modifica coincidencias de catálogo e importadas y reporta impacto.
5. Un principal admite varios complementos de un solo nivel.
6. Cada complemento permite cantidad por unidad o fija.
7. Reemplazar un principal retira sus complementos.
8. `Mobiliti` contiene una fila correcta por componente.
9. `Cotizacion` contiene una línea por principal, descripción compuesta e imágenes.
10. Los totales coinciden exactamente con la suma de los componentes.
11. No hay conversión, aumento ni descuento duplicado.
12. La plantilla oficial conserva fórmulas, formato, hojas ocultas y valores protegidos.
13. Un Proyecto grande se expande dinámicamente o falla antes de encolarse con un
    mensaje preciso.
14. Excel abre el archivo sin reparación ni advertencia de corrupción.
15. La validación se completa en localhost antes de cualquier despliegue.

## 18. Secuencia de entrega

1. Migración y repositorio de Proyectos.
2. Contrato de dominio, validadores y operaciones puras.
3. API de Proyectos, revisión e idempotencia.
4. Persistencia de importados y recursos.
5. Módulo Proyectos y guardado automático.
6. Selector reutilizado, sustituciones y complementos.
7. Expansión de instantánea hacia el motor.
8. Fórmulas de composición y montaje de imágenes.
9. Pruebas de regresión, estrés y apertura con Excel.
10. Validación manual en localhost.

Producción y SharePoint permanecen fuera de esta secuencia hasta nueva aprobación.
