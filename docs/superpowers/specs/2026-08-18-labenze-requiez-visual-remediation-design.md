# Diseño: remediación visual integral de Labenze y Requiez

Fecha: 2026-08-18  
Estado: aprobado por el usuario
Proyecto: Mobiliti SaaS Cotizador

## Objetivo

Dar a los 462 productos Labenze y 314 productos Requiez una imagen útil,
trazable y fiel al producto publicado. La cobertura final será 776/776 y no
quedará ningún `placeholder`.

La revisión de calidad aplica a **todas** las imágenes de ambos catálogos:
generadas, encontradas en Internet, compartidas por familia y extraídas de los
PDF. Una imagen `exact_pdf` no queda aprobada por el solo hecho de ser oficial;
también debe superar el contrato visual completo descrito abajo.

## Fuentes autoritativas

- Las identidades, códigos, configuraciones, descripciones y precios siguen
  proviniendo de los PDF B26 de Labenze y A-26 de Requiez almacenados en
  SharePoint.
- Los archivos fuente permanecen fijados por item ID, versión y SHA-256. Una
  versión nueva exige una actualización intencional; no se sustituye el PDF de
  forma silenciosa.
- La búsqueda visual usa, en este orden: sitio o API oficial del fabricante,
  distribuidores atribuibles con evidencia exacta del SKU, imagen oficial de la
  misma familia cuando la configuración sea visualmente equivalente y, sólo al
  agotarse esas opciones, generación de una imagen de referencia.
- No se aceptan coincidencias difusas, códigos parciales, sustituciones por un
  accesorio ni una imagen vecina que no represente la configuración publicada.
- Cada identidad conserva un `product_url` canónico separado de
  `image_source_url`. **Ver producto** debe abrir una ficha individual que
  resuelva por HTTP y demuestre el mismo SKU/configuración; nunca una portada,
  home, buscador, página de familia o archivo CDN aislado. Cuando el fabricante
  no publique una ficha individual, el fallback honesto es la URL estable del
  PDF fijado con `#page=<source_page>` y localizador de código. No se inventa una
  URL. Las imágenes generadas conservan este enlace de identidad y almacenan sus
  referencias visuales por separado.

## Contrato visual global

Cada imagen candidata, sin importar su origen, debe cumplir todos estos puntos:

1. El producto aparece completo; no se cortan respaldo, patas, base, brazos,
   cubierta, mesa, asientos ni accesorios que formen parte del SKU.
2. Existe margen visible en los cuatro lados y el objeto principal no toca los
   bordes del lienzo.
3. La vista permite reconocer el tipo y la configuración: número de plazas,
   base, brazos, cabecera, mesa, acabado estructural y demás rasgos que cambien
   la apariencia comercial.
4. No aparecen fragmentos de productos vecinos, rótulos del catálogo, códigos
   cortados, marcas de agua ajenas ni texto incrustado que compita con el
   producto.
5. El producto ocupa un área útil razonable y se presenta sobre fondo blanco o
   neutro limpio, sin deformación de proporciones.
6. La imagen no afirma una variante visual distinta. Una foto familiar sólo se
   comparte entre SKUs cuando la diferencia no es visible o la fuente declara
   explícitamente que representa esas variantes.
7. Cada asociación registra `reviewer`, `reviewed_at`,
   `full_product_visible=true`, `not_cropped=true`,
   `configuration_supported=true` y `approved=true`. La promoción queda
   bloqueada si falta uno de esos campos en cualquiera de las 776 asociaciones.
   Los controles automáticos ayudan a detectar bordes, dimensiones, hashes y
   duplicados, pero no sustituyen esta revisión semántica registrada.

El archivo final se normaliza a un lienzo PNG cuadrado de al menos 1024×1024,
sin reescalado no uniforme y conservando la relación de aspecto con tolerancia
máxima de 1 %. La caja visible del producto debe dejar al menos 4 % de margen por
lado, no exceder 92 % del ancho ni de la altura y ocupar entre 12 % y 80 % del
área del lienzo. La fuente debe tener al menos 512 píxeles en su lado menor antes
de normalizarse. Un caso oficial único que no alcance resolución u ocupación
puede pasar sólo con `quality_exception=true`, razón concreta y aprobación
manual; una excepción nunca permite recorte, contacto con bordes, deformación o
configuración incorrecta.

Por tanto, también se reauditan las 46 imágenes `exact_pdf` de Labenze, las 157
`exact_pdf` de Requiez, la imagen `family_pdf` de Requiez y las 416 asignaciones
`family_pdf` de Labenze. Si una extracción exacta falla el contrato, se busca una
imagen web exacta; si no existe, se genera una referencia y se muestra la
advertencia correspondiente.

## Clasificación y presentación pública

- `official`: imagen exacta del PDF, fabricante o fuente web exacta atribuible
  que pasó el contrato visual. No muestra advertencia de generación. La
  procedencia real no se infiere de este campo público: el manifiesto registra
  `source_kind` como `catalog_pdf`, `manufacturer_official`,
  `authorized_distributor` o `third_party_exact`, junto con URL, fecha, hash y
  evidencia de SKU. Un tercero no autorizado nunca se rotula como fabricante.
- `official` compartida: sólo se permite con un `shared_visual_group` explícito
  y evidencia oficial de que la imagen cubre todas las variantes, o una matriz
  campo por campo que demuestre igualdad de plazas, silueta, dimensiones
  visibles, base, brazos, cabecera, mesa, estructura, tapiz y color. Si cualquier
  campo visible difiere o no está probado, no se reutiliza: se busca la exacta y,
  agotada la búsqueda, se crea `generated_reference`.
- `generated_reference`: imagen creada después de documentar que no se encontró
  una imagen exacta válida. Muestra siempre el badge **Imagen de referencia** y
  conserva prompt, modelo, hashes, URLs de referencia y revisión.
- `placeholder`: estado transitorio permitido durante el trabajo, pero su conteo
  debe ser cero en el manifiesto promovible y en la aceptación final.

La advertencia de imagen generada debe permanecer visible en ficha, carrito y
cotización. El backend vuelve a hidratar el catálogo autoritativo antes de crear
el XLSX para que un cliente no pueda eliminarla alterando el payload.

## Flujo de curación

1. Reconstruir ambos catálogos desde los PDF fijados y enumerar 776 identidades.
2. Auditar cada visual actual contra el contrato global, incluidos los
   `exact_pdf` y todos los usos compartidos por familia.
3. Buscar por SKU exacto y variante en fuentes oficiales. Para Requiez se puede
   consultar su API pública, exigiendo igualdad normalizada de `code` o
   `shortCode`; no se promueve una coincidencia por nombre de familia.
4. Buscar en distribuidores atribuibles únicamente cuando la página demuestre el
   mismo SKU/configuración y permita enlazar la fuente estable.
5. Para los huecos restantes, generar una imagen a partir de la descripción,
   ficha oficial y productos vecinos comprobados de la misma colección. La
   generación no introduce texto, logos ni características no sustentadas.
6. Revisar individualmente cada candidato, registrar procedencia y asociarlo a
   su identidad estable. Un mismo archivo puede servir a varios SKUs sólo con
   equivalencia visual declarada y demostrada conforme a la matriz anterior.
7. Construir manifiestos completos y activos content-addressed por SHA-256;
   promover primero al almacén local con respaldo byte a byte.

Casos de aceptación obligatoria:

- `RM-9025N/NG` debe usar una fotografía web oficial completa en lugar del
  recorte del PDF mostrado por el usuario; no necesita generación.
- `RE-1063M`, `RE-1064M` y `RE-1073M` requieren búsqueda exacta. Si no aparece una
  foto exacta, se generan con mesa a partir de la ficha y los modelos JUN
  oficiales, y quedan marcadas como referencias generadas.
- Se sustituyen los recortes defectuosos conocidos de Requiez (`RM-9100/GR`,
  `RM-9100/NG`, `RM-9101/GR`, `RE-822/PU/MP`, `RE-828/PU/PN`, `RA-28`) y cualquier
  otro defecto descubierto en la auditoría total.
- Se sustituye el recorte incompleto Labenze `106-00603-BAT` y se revisan de
  manera especial componentes modulares ZELIG y configuraciones de bases,
  brazos y tapices.
- Los ocho registros Labenze `needs_review` conservan su ambigüedad comercial;
  una imagen no convierte un código ambiguo en identidad verificada.

## Arquitectura

- Los importadores de PDF permanecen deterministas y sin red ni generación en
  tiempo de sincronización.
- La investigación y curación producen manifiestos versionados separados para
  Labenze y Requiez. Cada fila enlaza identidad, tipo de imagen, archivo por hash,
  `product_url`, `image_source_url`, `source_kind`, evidencia de búsqueda,
  revisión y, cuando aplique, grupo visual compartido o metadatos de generación.
- Se reutiliza el pipeline de imágenes verificadas existente. Las extensiones se
  mantienen pequeñas: validación de manifiesto, promoción de activos y
  preservación curada durante futuras sincronizaciones.
- Una sincronización posterior puede reemplazar una referencia generada por una
  nueva imagen oficial exacta, pero nunca puede degradar una oficial exacta a
  familia, `placeholder` o generado.
- La preservación exige igualdad estricta de proveedor, `internal_id`,
  `product_key`, SKU y configuración visual. Cambios de precio o inventario no
  invalidan por sí solos la imagen.
- Los activos siguen en `catalog-assets` de Supabase y las cotizaciones en R2. No
  se empaquetan cientos de binarios en Vercel ni en Git.

## Pruebas y criterios de aceptación

El desarrollo sigue TDD: primero se agregan pruebas que fallen y luego el cambio
mínimo para hacerlas pasar.

- Manifiestos: exactamente 462 identidades Labenze y 314 Requiez, sin faltantes,
  extras ni `placeholder`.
- Procedencia: las oficiales demuestran SKU/configuración; las generadas tienen
  badge, prompt, modelo, referencias HTTPS, hashes y evidencia de búsqueda exacta
  agotada.
- Enlaces: las 776 identidades tienen `product_url` validado y separado del
  activo visual. La ficha individual debe corresponder estrictamente; el fallback
  al PDF incluye página/localizador. Se prueba que **Ver producto** nunca apunte a
  home, familia, búsqueda o CDN y que las URLs HTTP permitidas resuelvan.
- Calidad: todas las 776 asociaciones registran revisión del producto completo y
  margen; ningún `exact_pdf` está exento. Se verifican automáticamente los
  umbrales de resolución, margen, ocupación y aspecto, y se rechazan bordes
  tocados, recortes, dimensiones inválidas y duplicados no declarados. Las
  excepciones justificadas se enumeran y siguen necesitando aprobación.
- Integridad: PNG válido, MIME, dimensiones, SHA-256, nombre content-addressed y
  binding uno-a-uno con la identidad. Los duplicados perceptuales entre familias
  incompatibles fallan.
- Sincronización: el candidato oficial exacto gana; la curación sólo se preserva
  con identidad estricta; ningún campo comercial o referencia del PDF cambia.
- UI y XLSX: el badge y la advertencia de `generated_reference` aparecen una sola
  vez y no pueden suprimirse desde el navegador.
- E2E local: cotización mixta de los 13 proveedores con al menos un Labenze, un
  Requiez oficial y un Requiez generado; las imágenes embebidas corresponden a
  sus hashes y no aparecen nuevos `#REF!`.
- Auditoría visual final: revisión de una cuadrícula exportada de los 776
  productos y comprobaciones focales de los casos conocidos.

## Límites y presupuesto técnico

- Cada activo final debe quedar idealmente por debajo de 1 MiB y siempre por
  debajo del límite de 8 MiB del bucket; también respeta 8192×8192 y 25 millones
  de píxeles.
- No se suben binarios masivos mediante una función Vercel. Las respuestas de
  catálogo continúan paginadas y limitadas a 512 KiB.
- El lote incremental combinado se mide antes de promoverse y se bloquea por
  encima de 256 MiB salvo autorización explícita. Antes de producción se consulta
  el uso real de Supabase; no se infiere la cuota disponible.
- El input de cotización permanece bajo 25 MiB y el output E2E bajo 60 MiB, muy
  por debajo del límite de 150 MiB del worker. R2 no almacena los activos del
  catálogo.

## Despliegue y reversibilidad

Esta fase modifica y verifica sólo el worktree y el entorno local. Antes de
cada escritura o promoción se revalida la ruta del worktree, rama, HEAD,
upstream y estado sucio, y se registra el resultado sin tocar cambios ajenos.
Antes de promover, se respalda byte a byte `.mobiliti_dev_store/db.json`, el
manifiesto activo y los bindings previos bajo una carpeta timestamped en
`.mobiliti_dev_store/backups/visual-remediation/`. El reporte guarda rutas,
SHA-256, snapshot IDs anterior/nuevo y conteos de proyectos, reservaciones y
activos. La restauración de ese respaldo se prueba sobre staging antes de cambiar
el almacén activo. Los activos nuevos que queden sin referencia se conservan; si
alguna limpieza posterior se autoriza, se envían a la Papelera de reciclaje y
nunca se borran permanentemente. No se modifica SharePoint ni producción.

Al terminar cada hito se actualiza la bóveda Obsidian mediante MCP con worktree,
HEAD, versión de manifiestos, métricas, pruebas, respaldo y estado de despliegue.

El despliegue de migraciones, worker, API/web y activos en Supabase/R2 requiere
una autorización separada después de presentar métricas, auditoría visual y E2E
local.
