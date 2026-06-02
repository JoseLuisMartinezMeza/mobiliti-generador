import xlwings as xw
import time

FILE_PATH = r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\Formato Cotización 2026 GDL (1).xlsx"

def main():
    app = xw.App(visible=False)
    app.screen_updating = False
    app.calculation = 'manual'
    try:
        wb = app.books.open(FILE_PATH, update_links=False, read_only=True)
        time.sleep(1)

        # ============================================================
        # MOBILITI
        # ============================================================
        sh = wb.sheets["Mobiliti"]
        print("=== MOBILITI: Leyendo rango A1:G300 como array ===")
        data = sh.range("A1:G300").value
        print("=== Búsqueda de texto relevante en A1:G300 ===")
        for r_idx, row in enumerate(data, start=1):
            for c_idx, val in enumerate(row, start=1):
                if val and isinstance(val, str):
                    v = val.strip()
                    if len(v) > 2:
                        print(f"  Fila {r_idx}, Col {xw.utils.col_name(c_idx)} -> {v}")
        print()

        # Buscar matches de categorías
        categorias = [
            "Silla", "Mesas de Apoyo", "Escritorios", "Sillones",
            "Mesas de Juntas", "Librero", "Locker", "Gabinete",
            "Archiveros", "Phonebooths", "Multicontactos", "Terminados"
        ]
        print("=== Matches de categorías en A1:G300 ===")
        for r_idx, row in enumerate(data, start=1):
            for c_idx, val in enumerate(row, start=1):
                if val and isinstance(val, str):
                    v = val.strip().lower()
                    for cat in categorias:
                        if cat.lower() in v:
                            print(f"  Fila {r_idx}, Col {xw.utils.col_name(c_idx)} -> '{val.strip()}' (match: {cat})")
        print()

        # Leer filas 11-55, cols A-AF como array para ver estructura
        print("=== MOBILITI: Contenido filas 11-55, cols A-AF ===")
        data2 = sh.range("A11:AF55").value
        for r_idx, row in enumerate(data2, start=11):
            parts = []
            for c_idx, val in enumerate(row, start=1):
                if val is not None:
                    parts.append(f"{xw.utils.col_name(c_idx)}: {val}")
            if parts:
                print(f"  Fila {r_idx}: {' | '.join(parts[:8])}{' ...' if len(parts)>8 else ''}")
        print()

        # Fórmulas en filas específicas, cols F-AF
        print("=== MOBILITI: Fórmulas filas 14, 46, 47, 48 (F:AF) ===")
        for r in [14, 46, 47, 48]:
            print(f"--- Fila {r} ---")
            formulas = sh.range(f"F{r}:AF{r}").formula
            for c_idx, f in enumerate(formulas[0], start=6):
                if f and str(f).startswith("="):
                    print(f"  {xw.utils.col_name(c_idx)}{r}: {f}")
        print()

        # Subtotales Sección y sus fórmulas
        print("=== MOBILITI: Filas 'Subtotales Sección' (A1:A300) y fórmulas F:AF ===")
        col_a = sh.range("A1:A300").value
        for r_idx, val in enumerate(col_a, start=1):
            if val and isinstance(val, str) and "subtotales sección" in val.lower():
                print(f"Fila {r_idx}: {val.strip()}")
                formulas = sh.range(f"F{r_idx}:AF{r_idx}").formula[0]
                for c_idx, f in enumerate(formulas, start=6):
                    if f and str(f).startswith("="):
                        print(f"  {xw.utils.col_name(c_idx)}{r_idx}: {f}")
        print()

        # Sección X - NOMBRE
        print("=== MOBILITI: Filas 'Sección X - NOMBRE' ===")
        for r_idx, val in enumerate(col_a, start=1):
            if val and isinstance(val, str) and "sección" in val.lower() and "nombre" in val.lower():
                print(f"Fila {r_idx}: {val.strip()}")
        print()

        # ============================================================
        # FLETES
        # ============================================================
        sh_f = wb.sheets["Fletes"]
        print("=== FLETES: Named ranges con 'Mobiliario' ===")
        for nm in wb.names:
            try:
                if "mobiliario" in nm.name.lower():
                    print(f"  Named Range: {nm.name} -> {nm.refers_to}")
            except Exception:
                pass
        print()

        print("=== FLETES: Contenido A1:N34 ===")
        data_f = sh_f.range("A1:N34").value
        for r_idx, row in enumerate(data_f, start=1):
            parts = []
            for c_idx, val in enumerate(row, start=1):
                if val is not None:
                    parts.append(f"{xw.utils.col_name(c_idx)}: {val}")
            if parts:
                print(f"  Fila {r_idx}: {' | '.join(parts)}")
        print()

        # Validaciones en Fletes: solo revisar unas pocas celdas clave
        print("=== FLETES: Validaciones en celdas clave ===")
        celdas_clave = ["C5", "C6", "C7", "D5", "D6", "D7", "E5", "E6", "E7"]
        for addr in celdas_clave:
            try:
                cell = sh_f.range(addr)
                dv = cell.api.Validation
                if dv.Type != 0:
                    formula1 = ""
                    try:
                        formula1 = dv.Formula1
                    except Exception:
                        pass
                    print(f"  {addr}: Type={dv.Type}, Formula1={formula1}")
            except Exception:
                pass
        print()

        # Búsqueda global rápida de "Mobiliario" usando arrays
        print("=== BÚSQUEDA GLOBAL de 'Mobiliario' ===")
        for sheet in wb.sheets:
            try:
                # leer A1:T100 de cada hoja
                arr = sheet.range("A1:T100").value
                for r_idx, row in enumerate(arr, start=1):
                    for c_idx, val in enumerate(row, start=1):
                        if val and isinstance(val, str) and "mobiliario" in val.lower():
                            print(f"  Hoja '{sheet.name}', Fila {r_idx}, Col {xw.utils.col_name(c_idx)} -> {val.strip()}")
            except Exception:
                pass
        print()

        wb.close()
    finally:
        app.quit()

if __name__ == "__main__":
    main()
