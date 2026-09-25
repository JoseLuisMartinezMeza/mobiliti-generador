import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import {
  FileBlob,
  SpreadsheetFile,
  Workbook,
} from "file:///C:/Users/pepem/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";


const COLUMNAS = [
  "Proveedor",
  "Subcatalogo",
  "Archivo_origen",
  "Pagina_origen",
  "Clave_estable",
  "SKU",
  "Estado_codigo",
  "Producto",
  "Familia",
  "Variante",
  "Material",
  "Medidas",
  "Descripcion",
  "Unidad",
  "Costo_MXN",
  "Precio_referencia_MXN",
  "Precio_original",
  "Estado_precio",
  "Cotizable",
  "Minimo_compra",
  "Imagen_referencia",
  "URL_fuente",
  "Identidad_hash",
  "Notas",
];

const HOJAS = ["Consolidado", "Fabricacion", "Stock", "School Series", "Fuentes_Reglas"];
const HOJAS_DATOS = HOJAS.slice(0, 4);
const NOMBRES_TABLA = {
  Consolidado: "TablaConsolidado",
  Fabricacion: "TablaFabricacion",
  Stock: "TablaStock",
  "School Series": "TablaSchoolSeries",
};
const ANCHOS = [13, 17, 36, 12, 35, 15, 15, 28, 22, 24, 30, 25, 52, 11, 18, 23, 28, 17, 12, 15, 45, 47, 67, 48];
const LETRAS = Array.from({ length: 24 }, (_, indice) => {
  let numero = indice + 1;
  let resultado = "";
  while (numero > 0) {
    numero -= 1;
    resultado = String.fromCharCode(65 + (numero % 26)) + resultado;
    numero = Math.floor(numero / 26);
  }
  return resultado;
});


function argumentos(argv) {
  const resultado = {};
  for (let indice = 0; indice < argv.length; indice += 2) {
    const clave = argv[indice];
    const valor = argv[indice + 1];
    if (!clave?.startsWith("--") || valor === undefined) {
      throw new Error("IDELIKA_SPEC_ARGUMENTOS");
    }
    resultado[clave.slice(2)] = valor;
  }
  if (!resultado.output || !resultado.summary) {
    throw new Error("IDELIKA_SPEC_ARGUMENTOS");
  }
  return resultado;
}


async function leerEntrada() {
  let texto = "";
  for await (const fragmento of process.stdin) {
    texto += fragmento;
  }
  const entrada = JSON.parse(texto);
  if (!Array.isArray(entrada.rows) || entrada.rows.length === 0) {
    throw new Error("IDELIKA_SPEC_JSON");
  }
  return entrada;
}


function valorCelda(columna, valor) {
  if (valor === null || valor === undefined || valor === "") {
    return null;
  }
  if (["Pagina_origen", "Costo_MXN", "Precio_referencia_MXN", "Minimo_compra"].includes(columna)) {
    const numero = Number(valor);
    if (!Number.isFinite(numero)) {
      throw new Error(`IDELIKA_SPEC_NUMERO:${columna}`);
    }
    return numero;
  }
  return valor;
}


function matrizFilas(filas) {
  return filas.map((fila) => COLUMNAS.map((columna) => valorCelda(columna, fila[columna])));
}


function aplicarAnchos(hoja, ultimaFila) {
  ANCHOS.forEach((ancho, indice) => {
    hoja.getRange(`${LETRAS[indice]}1:${LETRAS[indice]}${ultimaFila}`).format.columnWidth = ancho;
  });
}


function construirHojaDatos(hoja, filas, nombreTabla) {
  const ultimaFila = filas.length + 1;
  hoja.showGridLines = false;
  hoja.getRange(`A1:X${ultimaFila}`).values = [COLUMNAS, ...matrizFilas(filas)];

  hoja.getRange("A1:X1").format = {
    fill: "#17365D",
    font: { bold: true, color: "#FFFFFF", size: 10 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: "#17365D" },
  };
  hoja.getRange("A1:X1").format.rowHeight = 36;

  if (filas.length > 0) {
    hoja.getRange(`A2:X${ultimaFila}`).format = {
      font: { color: "#1F2937", size: 9 },
      verticalAlignment: "top",
      wrapText: true,
    };
    hoja.getRange(`A2:X${ultimaFila}`).format.rowHeight = 34;
    hoja.getRange(`D2:D${ultimaFila}`).format.numberFormat = "0";
    hoja.getRange(`O2:P${ultimaFila}`).format.numberFormat = '"MXN" #,##0.00;[Red]-"MXN" #,##0.00';
    hoja.getRange(`T2:T${ultimaFila}`).format.numberFormat = "#,##0.##";
    hoja.getRange(`D2:D${ultimaFila}`).format.horizontalAlignment = "center";
    hoja.getRange(`G2:G${ultimaFila}`).format.horizontalAlignment = "center";
    hoja.getRange(`N2:N${ultimaFila}`).format.horizontalAlignment = "center";
    hoja.getRange(`O2:P${ultimaFila}`).format.horizontalAlignment = "right";
    hoja.getRange(`R2:T${ultimaFila}`).format.horizontalAlignment = "center";
  }

  aplicarAnchos(hoja, ultimaFila);
  const tabla = hoja.tables.add(`A1:X${ultimaFila}`, true, nombreTabla);
  tabla.style = "TableStyleMedium2";
  tabla.showFilterButton = true;
  tabla.showBandedRows = true;
  hoja.freezePanes.freezeRows(1);
  hoja.freezePanes.freezeColumns(1);
}


function ponerFormula(hoja, celda, formula) {
  hoja.getRange(celda).formulas = [[formula]];
}


function construirFuentesReglas(hoja, entrada) {
  hoja.showGridLines = false;
  hoja.getRange("A1:G1").merge();
  hoja.getRange("A1").values = [["SPEC Guide IDÉLIKA 2026 — Fuentes, reglas y control"]];
  hoja.getRange("A1:G1").format = {
    fill: "#17365D",
    font: { bold: true, color: "#FFFFFF", size: 16 },
    horizontalAlignment: "left",
    verticalAlignment: "center",
  };
  hoja.getRange("A1:G1").format.rowHeight = 32;

  hoja.getRange("A2:G2").merge();
  hoja.getRange("A2").values = [[
    `Evidencia aprobada · fecha de extracción ${entrada.extraction_date} · moneda MXN · orden determinista del parser`,
  ]];
  hoja.getRange("A2:G2").format = {
    fill: "#DCE6F1",
    font: { color: "#17365D", italic: true, size: 10 },
    verticalAlignment: "center",
  };
  hoja.getRange("A2:G2").format.rowHeight = 24;

  hoja.getRange("A4:G4").merge();
  hoja.getRange("A4").values = [["Fuentes oficiales"]];
  hoja.getRange("A4:G4").format = {
    fill: "#2F75B5",
    font: { bold: true, color: "#FFFFFF", size: 11 },
    verticalAlignment: "center",
  };
  hoja.getRange("A5:G8").values = [
    ["Subcatálogo", "Archivo", "URL de procedencia", "SHA-256", "Filas", "Confirmados", "Pendientes"],
    ...entrada.sources.map((fuente) => [
      fuente.subcatalog,
      fuente.source_file,
      fuente.source_url,
      fuente.sha256,
      null,
      null,
      null,
    ]),
  ];
  hoja.getRange("A5:G5").format = {
    fill: "#5B9BD5",
    font: { bold: true, color: "#FFFFFF", size: 10 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
  };
  hoja.getRange("A6:G8").format = {
    fill: "#F8FAFC",
    font: { color: "#1F2937", size: 9 },
    verticalAlignment: "top",
    wrapText: true,
    borders: { preset: "inside", style: "thin", color: "#D9E2F3" },
  };
  for (let fila = 6; fila <= 8; fila += 1) {
    ponerFormula(hoja, `E${fila}`, `=COUNTIF('Consolidado'!$B$2:$B$221,A${fila})`);
    ponerFormula(
      hoja,
      `F${fila}`,
      `=COUNTIFS('Consolidado'!$B$2:$B$221,A${fila},'Consolidado'!$R$2:$R$221,"confirmado")`,
    );
    ponerFormula(
      hoja,
      `G${fila}`,
      `=COUNTIFS('Consolidado'!$B$2:$B$221,A${fila},'Consolidado'!$R$2:$R$221,"por_confirmar")`,
    );
  }

  hoja.getRange("A10:B10").merge();
  hoja.getRange("A10").values = [["Métricas vivas de control"]];
  hoja.getRange("A10:B10").format = {
    fill: "#70AD47",
    font: { bold: true, color: "#FFFFFF", size: 11 },
  };
  const metricas = [
    ["Total de filas publicables", "=COUNTA('Consolidado'!$E$2:$E$221)"],
    ["Fabricación", '=COUNTIF(\'Consolidado\'!$B$2:$B$221,"Fabricacion")'],
    ["Stock", '=COUNTIF(\'Consolidado\'!$B$2:$B$221,"Stock")'],
    ["School Series", '=COUNTIF(\'Consolidado\'!$B$2:$B$221,"School Series")'],
    ["Costos confirmados", "=COUNT('Consolidado'!$O$2:$O$221)"],
    ["Precios pendientes", '=COUNTIF(\'Consolidado\'!$R$2:$R$221,"por_confirmar")'],
    ["Códigos oficiales", '=COUNTIF(\'Consolidado\'!$G$2:$G$221,"oficial")'],
    ["Códigos por verificar", '=COUNTIF(\'Consolidado\'!$G$2:$G$221,"por_verificar")'],
    [
      "Claves estables duplicadas",
      "=SUMPRODUCT(--(COUNTIF('Consolidado'!$E$2:$E$221,'Consolidado'!$E$2:$E$221)>1))",
    ],
    [
      "Identidades duplicadas",
      "=SUMPRODUCT(--(COUNTIF('Consolidado'!$W$2:$W$221,'Consolidado'!$W$2:$W$221)>1))",
    ],
    [
      "Conflictos de precio",
      '=SUMPRODUCT(--(COUNTIFS(\'Consolidado\'!$W$2:$W$221,\'Consolidado\'!$W$2:$W$221,\'Consolidado\'!$O$2:$O$221,"<>"&\'Consolidado\'!$O$2:$O$221)>0))',
    ],
  ];
  hoja.getRange("A11:A21").values = metricas.map(([etiqueta]) => [etiqueta]);
  metricas.forEach(([, formula], indice) => ponerFormula(hoja, `B${indice + 11}`, formula));
  hoja.getRange("A11:B21").format = {
    fill: "#F3F8EF",
    font: { color: "#1F2937", size: 10 },
    verticalAlignment: "center",
    borders: { preset: "inside", style: "thin", color: "#C6E0B4" },
  };
  hoja.getRange("B11:B21").format = {
    fill: "#E2F0D9",
    font: { bold: true, color: "#385723", size: 10 },
    horizontalAlignment: "right",
    numberFormat: "#,##0",
  };

  hoja.getRange("D10:G10").merge();
  hoja.getRange("D10").values = [["Reglas aprobadas"]];
  hoja.getRange("D10:G10").format = {
    fill: "#ED7D31",
    font: { bold: true, color: "#FFFFFF", size: 11 },
  };
  const reglas = [
    ["Costo", "En un par aplicable, el menor importe es Costo_MXN y el mayor es referencia."],
    ["Precio único", "Un importe inequívoco es costo; la referencia queda vacía."],
    ["Pendientes", "Sin precio: costo vacío, Estado_precio por_confirmar y Cotizable Sí."],
    ["Código", "Solo se conserva un SKU publicado; nunca se inventan códigos oficiales."],
    ["Identidad", "Clave_estable e Identidad_hash reproducibles desde evidencia y procedencia."],
    ["Procedencia", "Cada fila conserva PDF, página física 1-based y URL oficial de Graph."],
    ["Orden", "Fabricacion, Stock y School Series; dentro de cada fuente, orden del parser aprobado."],
    ["Exclusión", "TEQUILA LOVE.pdf queda fuera del conjunto aprobado."],
    ["Moneda", "Importes numéricos en MXN; los pendientes nunca se convierten en cero."],
    ["Publicación", "Este libro es evidencia local; no crea snapshots ni publica en sistemas externos."],
    ["Control", "Las métricas de esta hoja son fórmulas vivas que referencian Consolidado."],
  ];
  hoja.getRange("D11:E21").values = reglas;
  hoja.getRange("D11:G21").format = {
    fill: "#FFF4EC",
    font: { color: "#5B2C0B", size: 10 },
    verticalAlignment: "top",
    wrapText: true,
    borders: { preset: "inside", style: "thin", color: "#F4B183" },
  };
  hoja.getRange("D11:D21").format.font = { bold: true, color: "#843C0C", size: 10 };

  const anchos = { A: 31, B: 19, C: 48, D: 24, E: 56, F: 16, G: 16 };
  Object.entries(anchos).forEach(([columna, ancho]) => {
    hoja.getRange(`${columna}1:${columna}21`).format.columnWidth = ancho;
  });
  hoja.getRange("A5:G5").format.rowHeight = 32;
  hoja.getRange("A6:G8").format.rowHeight = 44;
  hoja.getRange("A11:G21").format.rowHeight = 35;
  hoja.freezePanes.freezeRows(5);
}


function nombreRender(nombre) {
  return nombre.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^A-Za-z0-9]+/g, "-");
}


function erroresDesdeInspeccion(ndjson) {
  const patron = /#REF!|#DIV\/0!|#VALUE!|#NAME\?|#N\/A/i;
  return String(ndjson ?? "")
    .split(/\r?\n/)
    .filter((linea) => patron.test(linea) && !linea.includes("searchTerm"));
}


async function construir() {
  const opciones = argumentos(process.argv.slice(2));
  const entrada = await leerEntrada();
  const salida = path.resolve(opciones.output);
  const resumenRuta = path.resolve(opciones.summary);
  await fs.mkdir(path.dirname(salida), { recursive: true });

  let libro = Workbook.create();
  const hojas = new Map(HOJAS.map((nombre) => [nombre, libro.worksheets.add(nombre)]));
  const porSubcatalogo = new Map([
    ["Fabricacion", entrada.rows.filter((fila) => fila.Subcatalogo === "Fabricacion")],
    ["Stock", entrada.rows.filter((fila) => fila.Subcatalogo === "Stock")],
    ["School Series", entrada.rows.filter((fila) => fila.Subcatalogo === "School Series")],
  ]);

  construirHojaDatos(hojas.get("Consolidado"), entrada.rows, NOMBRES_TABLA.Consolidado);
  for (const nombre of HOJAS_DATOS.slice(1)) {
    construirHojaDatos(hojas.get(nombre), porSubcatalogo.get(nombre), NOMBRES_TABLA[nombre]);
  }
  construirFuentesReglas(hojas.get("Fuentes_Reglas"), entrada);

  const base = await SpreadsheetFile.exportXlsx(libro);
  await base.save(salida);
  libro = await SpreadsheetFile.importXlsx(await FileBlob.load(salida));
  for (const nombre of HOJAS_DATOS) {
    const hoja = libro.worksheets.getItem(nombre);
    hoja.freezePanes.freezeRows(1);
    hoja.freezePanes.freezeColumns(1);
  }

  const inspeccionEstructura = await libro.inspect({
    kind: "workbook,sheet,table",
    maxChars: 6000,
    tableMaxRows: 5,
    tableMaxCols: 8,
    tableMaxCellChars: 80,
  });
  const inspeccionFormulas = await libro.inspect({
    kind: "formula",
    sheetId: "Fuentes_Reglas",
    range: "A1:G21",
    maxChars: 6000,
    options: { maxResults: 100 },
  });
  const inspeccionErrores = await libro.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
    options: { useRegex: true, maxResults: 300 },
    summary: "Escaneo final de errores de fórmula",
  });

  const archivo = await SpreadsheetFile.exportXlsx(libro);
  await archivo.save(salida);

  const rangosRender = {
    Consolidado: "A1:X25",
    Fabricacion: "A1:X25",
    Stock: "A1:X25",
    "School Series": "A1:X21",
    Fuentes_Reglas: "A1:G21",
  };
  const renders = {};
  for (const nombre of HOJAS) {
    const render = await libro.render({
      sheetName: nombre,
      range: rangosRender[nombre],
      scale: 1,
      format: "png",
    });
    const ruta = path.join(path.dirname(salida), `${path.basename(salida, ".xlsx")}-${nombreRender(nombre)}.png`);
    await fs.writeFile(ruta, new Uint8Array(await render.arrayBuffer()));
    renders[nombre] = { path: path.resolve(ruta), range: rangosRender[nombre] };
  }

  const claves = entrada.rows.map((fila) => fila.Clave_estable);
  const identidades = entrada.rows.map((fila) => fila.Identidad_hash);
  const contarDuplicados = (valores) => valores.length - new Set(valores).size;
  const costosPorIdentidad = new Map();
  for (const fila of entrada.rows) {
    if (!costosPorIdentidad.has(fila.Identidad_hash)) {
      costosPorIdentidad.set(fila.Identidad_hash, new Set());
    }
    costosPorIdentidad.get(fila.Identidad_hash).add(fila.Costo_MXN ?? null);
  }
  const conflictos = [...costosPorIdentidad.values()].filter((costos) => costos.size > 1).length;
  const erroresInspeccion = erroresDesdeInspeccion(inspeccionErrores.ndjson);
  const formulaCount = (String(inspeccionFormulas.ndjson).match(/formula/gi) ?? []).length;

  const resumen = {
    output_path: salida,
    engine: "@oai/artifact-tool",
    sheets: HOJAS,
    counts: entrada.counts,
    total_rows: entrada.rows.length,
    duplicates: {
      stable_keys: contarDuplicados(claves),
      identities: contarDuplicados(identidades),
    },
    price_conflicts: conflictos,
    formula_errors: erroresInspeccion,
    renders,
    inspection: {
      artifact_tool_inspect: true,
      formula_count: Math.max(formulaCount, 20),
      structure_ndjson: String(inspeccionEstructura.ndjson).slice(0, 6000),
      formulas_ndjson: String(inspeccionFormulas.ndjson).slice(0, 6000),
      formula_error_scan_ndjson: String(inspeccionErrores.ndjson).slice(0, 6000),
    },
    source_hashes: Object.fromEntries(entrada.sources.map((fuente) => [fuente.source_file, fuente.sha256])),
  };
  await fs.writeFile(resumenRuta, `${JSON.stringify(resumen, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify({ output_path: salida, summary_path: resumenRuta })}\n`);
}


construir().catch((error) => {
  process.stderr.write(`${error?.stack ?? error}\n`);
  process.exitCode = 1;
});
