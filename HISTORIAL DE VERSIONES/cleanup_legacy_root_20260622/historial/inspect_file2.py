import xlwings as xw
import time

FILE_PATH = r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\Formato Cotización 2026 GDL (1).xlsx"

def safe_used_range(sheet):
    try:
        ur = sheet.used_range
        if ur is None:
            return None
        return ur
    except Exception:
        return None

def main():
    app = xw.App(visible=False)
    app.screen_updating = False
    app.calculation = 'manual'
    try:
        wb = app.books.open(FILE_PATH, update_links=False, read_only=True)
        time.sleep(1)  # dar tiempo a Excel

        sh = wb.sheets["Mobiliti"]
        ur = safe_used_range(sh)
        max_row = ur.last_cell.row if ur else 610
        print("=== MOBILITI: Búsqueda de categorías en col A-G, filas 1-300 ===")
        cats_found = []
        for r in range(1, min(301, max_row + 1)):
            for c in range(1, 8):
                val = sh.cells(r, c).value
                if val and isinstance(val, str):
                    v = val.strip()
                    if len(v) > 2 and not v.startswith("="):
                        cats_found.append((r, c, v))
        for item in cats_found:
            print(f"  Fila {item[0]}, Col {item[1]} ({xw.utils.col_name(item[1])}) -> {item[2]}")
        print()

        print("=== MOBILITI: Matches exactos/substring de categorías en col A-G ===")
        categorias = [
            "Silla", "Mesas de Apoyo", "Escritorios", "Sillones",
            "Mesas de Juntas", "Librero", "Locker", "Gabinete",
            "Archiveros", "Phonebooths", "Multicontactos", "Terminados"
        ]
        for r in range(1, min(301, max_row + 1)):
            for c in range(1, 8):
                val = sh.cells(r, c).value
                if val and isinstance(val, str):
                    v = val.strip().lower()
                    for cat in categorias:
                        if cat.lower() in v:
                            print(f"  Fila {r}, Col {xw.utils.col_name(c)} -> '{val.strip()}' (match: {cat})")
        print()

        print("=== MOBILITI: Contenido filas 11-55, cols A-AF (valores no vacíos) ===")
        for r in range(11, 56):
            row_vals = []
            for c in range(1, 33):
                v = sh.cells(r, c).value
                if v is not None:
                    row_vals.append(f"{xw.utils.col_name(c)}: {v}")
            if row_vals:
                print(f"  Fila {r}: {' | '.join(row_vals[:8])}{' ...' if len(row_vals)>8 else ''}")
        print()

        print("=== MOBILITI: Fórmulas en filas 14, 46, 47, 48 (cols F-AF) ===")
        for r in [14, 46, 47, 48]:
            print(f"--- Fila {r} ---")
            for c in range(6, 33):
                f = sh.cells(r, c).formula
                if f and str(f).startswith("="):
                    print(f"  {xw.utils.col_name(c)}{r}: {f}")
        print()

        print("=== MOBILITI: Filas de 'Subtotales Sección' y sus fórmulas ===")
        for r in range(1, min(301, max_row + 1)):
            v = sh.cells(r, 1).value
            if v and isinstance(v, str) and "subtotales sección" in v.lower():
                print(f"Fila {r}: {v.strip()}")
                for c in range(6, 33):
                    f = sh.cells(r, c).formula
                    if f and str(f).startswith("="):
                        print(f"  {xw.utils.col_name(c)}{r}: {f}")
        print()

        print("=== MOBILITI: Filas de 'Sección X - NOMBRE' ===")
        for r in range(1, min(301, max_row + 1)):
            v = sh.cells(r, 1).value
            if v and isinstance(v, str) and "sección" in v.lower() and "nombre" in v.lower():
                print(f"Fila {r}: {v.strip()}")
        print()

        # FLETES
        sh_f = wb.sheets["Fletes"]
        print("=== FLETES: Named ranges que contienen 'Mobiliario' ===")
        for nm in wb.names:
            try:
                if "mobiliario" in nm.name.lower():
                    print(f"  Named Range: {nm.name} -> {nm.refers_to}")
            except Exception:
                pass
        print()

        print("=== FLETES: Revisando celdas en columna C y D (filas 1-34) ===")
        ur_f = safe_used_range(sh_f)
        max_row_f = ur_f.last_cell.row if ur_f else 34
        for r in range(1, max_row_f + 1):
            for c in [3, 4]:
                val = sh_f.cells(r, c).value
                if val is not None:
                    print(f"  Fila {r}, Col {xw.utils.col_name(c)} -> {val}")
        print()

        print("=== FLETES: Validaciones via API ===")
        count = 0
        if ur_f:
            for cell in ur_f:
                try:
                    dv = cell.api.Validation
                    if dv.Type != 0:
                        t = dv.Type
                        formula1 = ""
                        try:
                            formula1 = dv.Formula1
                        except Exception:
                            pass
                        print(f"  {cell.address}: Type={t}, Formula1={formula1}")
                        count += 1
                        if count > 30:
                            print("  (más validaciones omitidas...)")
                            break
                except Exception:
                    pass
        if count == 0:
            print("  No se encontraron validaciones en el rango usado.")
        print()

        print("=== BÚSQUEDA GLOBAL de 'Mobiliario' en TODO el libro ===")
        for sheet in wb.sheets:
            try:
                ur_s = safe_used_range(sheet)
                mr = ur_s.last_cell.row if ur_s else 100
                mc = ur_s.last_cell.column if ur_s else 20
                for r in range(1, min(301, mr + 1)):
                    for c in range(1, min(21, mc + 1)):
                        val = sheet.cells(r, c).value
                        if val and isinstance(val, str) and "mobiliario" in val.lower():
                            print(f"  Hoja '{sheet.name}', Fila {r}, Col {xw.utils.col_name(c)} -> {val.strip()}")
            except Exception:
                pass
        print()

        wb.close()
    finally:
        app.quit()

if __name__ == "__main__":
    main()
