import xlwings as xw
import sys

FILE_PATH = r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\Formato Cotización 2026 GDL (1).xlsx"

def main():
    app = xw.App(visible=False)
    try:
        wb = app.books.open(FILE_PATH, update_links=False, read_only=True)
        print("=== HOJAS DEL ARCHIVO ===")
        for s in wb.sheets:
            print(f"  - {s.name}")
        print()

        # ============================================================
        # HOJA MOBILITI
        # ============================================================
        if "Mobiliti" in [s.name for s in wb.sheets]:
            sh = wb.sheets["Mobiliti"]
            print("=== HOJA: Mobiliti ===")
            print(f"Usada range: {sh.used_range.address}")
            print(f"Filas usadas: {sh.used_range.last_cell.row}")
            print()

            # Buscar categorías conocidas en la hoja
            categorias = [
                "Silla", "Mesas de Apoyo", "Escritorios", "Sillones",
                "Mesas de Juntas", "Librero - Locker - Gabinete",
                "Archiveros Moviles y Fijos", "Phonebooths", "Multicontactos", "Terminados"
            ]

            # Asumimos que las categorías están en alguna columna entre A y E,
            # escaneamos las primeras 200 filas para encontrarlas.
            max_row = min(sh.used_range.last_cell.row, 300)
            cat_locations = {}
            for r in range(1, max_row + 1):
                for c in range(1, 6):  # A-E
                    val = sh.cells(r, c).value
                    if val and isinstance(val, str):
                        v_clean = val.strip()
                        for cat in categorias:
                            if cat.lower() in v_clean.lower():
                                if cat not in cat_locations:
                                    cat_locations[cat] = []
                                cat_locations[cat].append((r, c))

            print("--- Ubicación aproximada de categorías (primer match por cat) ---")
            for cat in categorias:
                if cat in cat_locations:
                    print(f"  {cat}: fila {cat_locations[cat][0][0]}, col {cat_locations[cat][0][1]} ({cat_locations[cat][0]})")
                else:
                    print(f"  {cat}: NO ENCONTRADA")
            print()

            # Intentar detectar filas donde hay texto de categoría en columna B (2) o A (1)
            print("--- Búsqueda detallada de texto 'categoría' en columnas A-B (filas 1-250) ---")
            detalle_cats = []
            for r in range(1, min(251, max_row + 1)):
                for c in [1, 2]:
                    val = sh.cells(r, c).value
                    if val and isinstance(val, str):
                        v = val.strip()
                        # Detectar si parece nombre de categoría (no vacío, no numérico, longitud > 2)
                        if len(v) > 2:
                            detalle_cats.append((r, c, v))
            for item in detalle_cats[:60]:
                print(f"  Fila {item[0]}, Col {item[1]} -> {item[2]}")
            if len(detalle_cats) > 60:
                print(f"  ... y {len(detalle_cats)-60} más")
            print()

            # Escanear fórmulas en columnas F a AF (6 a 32) para las primeras filas de cada bloque detectado
            print("--- Fórmulas en columnas F:AF para filas clave ---")
            filas_clave = []
            if cat_locations:
                for cat, locs in cat_locations.items():
                    filas_clave.extend([l[0] for l in locs])
                filas_clave = sorted(set(filas_clave))
            else:
                # fallback: primeras 20 filas
                filas_clave = list(range(1, 21))

            for r in filas_clave[:15]:
                formulas = []
                for c in range(6, 33):  # F=6 a AF=32
                    f = sh.cells(r, c).formula
                    if f and f.startswith("="):
                        col_letter = xw.utils.col_name(c)
                        formulas.append(f"{col_letter}{r}: {f}")
                if formulas:
                    print(f"  Fila {r}:")
                    for fo in formulas[:10]:
                        print(f"    {fo}")
                    if len(formulas) > 10:
                        print(f"    ... y {len(formulas)-10} más")
            print()

            # Buscar subtotales (texto que contenga "subtotal" o "total")
            print("--- Búsqueda de subtotales / totales (filas 1-300) ---")
            subtotales = []
            for r in range(1, min(301, max_row + 1)):
                for c in range(1, 6):
                    val = sh.cells(r, c).value
                    if val and isinstance(val, str):
                        v = val.strip().lower()
                        if "subtotal" in v or "total" in v:
                            subtotales.append((r, c, val.strip()))
            for sbt in subtotales[:30]:
                print(f"  Fila {sbt[0]}, Col {sbt[1]} -> {sbt[2]}")
            print()

            # Detectar secciones de productos basadas en rangos usados
            print("--- Rangos usados por bloques (intento heurístico) ---")
            # Simplemente mostrar used_range no basta; mostraremos filas donde la columna A o B cambian de vacío a contenido
            print("Hecho arriba con detalle_cats")
            print()

        # ============================================================
        # HOJA FLETES
        # ============================================================
        if "Fletes" in [s.name for s in wb.sheets]:
            sh_f = wb.sheets["Fletes"]
            print("=== HOJA: Fletes ===")
            print(f"Usada range: {sh_f.used_range.address}")
            print(f"Filas usadas: {sh_f.used_range.last_cell.row}")
            print()

            # Buscar validaciones de datos
            print("--- Validaciones de datos en Fletes ---")
            # xlwings no expone directamente validation objects de forma simple,
            # pero podemos usar el API de Excel via sh.api
            validations = []
            try:
                used = sh_f.used_range
                for cell in used:
                    try:
                        dv = cell.api.Validation
                        if dv.Type:  # tiene validación
                            formula1 = ""
                            try:
                                formula1 = dv.Formula1
                            except Exception:
                                pass
                            validations.append((cell.address, dv.Type, formula1))
                    except Exception:
                        pass
            except Exception as e:
                print(f"Error leyendo validaciones: {e}")

            if validations:
                for v in validations[:20]:
                    print(f"  Celda {v[0]} -> Tipo {v[1]}, Formula1: {v[2]}")
                if len(validations) > 20:
                    print(f"  ... y {len(validations)-20} más")
            else:
                print("  No se encontraron validaciones via API directo (o método no soportado).")
            print()

            # Buscar texto "Mobiliario" en Fletes
            print("--- Búsqueda de 'Mobiliario' en Fletes ---")
            for r in range(1, sh_f.used_range.last_cell.row + 1):
                for c in range(1, sh_f.used_range.last_cell.column + 1):
                    val = sh_f.cells(r, c).value
                    if val and isinstance(val, str) and "mobiliario" in val.lower():
                        print(f"  Encontrado en Fila {r}, Col {c}: '{val}'")
            print()

            # Listar contenido de las primeras filas para entender estructura
            print("--- Contenido primeras 30 filas, cols A-H ---")
            for r in range(1, min(31, sh_f.used_range.last_cell.row + 1)):
                row_vals = []
                for c in range(1, 9):
                    v = sh_f.cells(r, c).value
                    if v is not None:
                        row_vals.append(f"{xw.utils.col_name(c)}: {v}")
                if row_vals:
                    print(f"  Fila {r}: {' | '.join(row_vals)}")
            print()

        wb.close()
    finally:
        app.quit()

if __name__ == "__main__":
    main()
