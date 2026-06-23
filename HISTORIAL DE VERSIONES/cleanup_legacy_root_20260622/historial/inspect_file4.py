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
        sh = wb.sheets["Mobiliti"]

        print("=== MOBILITI: Validaciones en celdas clave (E14, F14, E49, F49, etc.) ===")
        for r in [14, 49, 84, 119, 154, 189, 224, 259, 294]:
            for c in [5, 6]:  # E, F
                addr = f"{xw.utils.col_name(c)}{r}"
                try:
                    cell = sh.range(addr)
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

        print("=== MOBILITI: Contenido de columna E (Producto) en filas de inicio de sección ===")
        for r in [13, 48, 83, 118, 153, 188, 223, 258, 293]:
            val = sh.cells(r, 5).value
            print(f"  Fila {r}, Col E -> {val}")
        print()

        print("=== MOBILITI: Contenido de columna D en filas de inicio de sección ===")
        for r in [13, 48, 83, 118, 153, 188, 223, 258, 293]:
            val = sh.cells(r, 4).value
            print(f"  Fila {r}, Col D -> {val}")
        print()

        print("=== MOBILITI: Búsqueda de categorías en columnas E-J, filas 1-350 ===")
        categorias = [
            "Silla", "Mesas de Apoyo", "Escritorios", "Sillones",
            "Mesas de Juntas", "Librero", "Locker", "Gabinete",
            "Archiveros", "Phonebooths", "Multicontactos", "Terminados"
        ]
        data = sh.range("A1:J350").value
        for r_idx, row in enumerate(data, start=1):
            for c_idx, val in enumerate(row, start=1):
                if val and isinstance(val, str):
                    v = val.strip().lower()
                    for cat in categorias:
                        if cat.lower() in v:
                            print(f"  Fila {r_idx}, Col {xw.utils.col_name(c_idx)} -> '{val.strip()}' (match: {cat})")
        print()

        print("=== MOBILITI: Estructura de secciones ===")
        col_a = sh.range("A1:A350").value
        secciones = []
        for r_idx, val in enumerate(col_a, start=1):
            if val and isinstance(val, str):
                v = val.strip().lower()
                if "sección" in v and "nombre" in v:
                    secciones.append({"nombre_fila": r_idx, "nombre": val.strip()})
                elif "subtotales sección" in v:
                    if secciones:
                        secciones[-1]["subtotal_fila"] = r_idx
                        secciones[-1]["subtotal_nombre"] = val.strip()
        for sec in secciones:
            prod_start = sec["nombre_fila"] + 1
            subtotal = sec.get("subtotal_fila", "?")
            print(f"  {sec['nombre']} -> fila nombre: {sec['nombre_fila']}, productos: {prod_start}-{subtotal-1 if subtotal!='?' else '?'}, subtotal: {subtotal}")
        print()

        print("=== FLETES: Contenido del Named Range 'Mobiliario' ===")
        # wb.names["Mobiliario"].refers_to es =Fletes!$I$6:$I$19
        # Leer directamente ese rango
        try:
            mob_range = wb.names["Mobiliario"].refers_to_range
            vals = mob_range.value
            print(f"  Rango: {mob_range.address}")
            for i, v in enumerate(vals, start=6):
                print(f"    I{i}: {v}")
        except Exception as e:
            print(f"  Error leyendo Mobiliario: {e}")
        print()

        print("=== FLETES: Validaciones en columna I (Mobiliario) ===")
        for r in range(6, 20):
            addr = f"I{r}"
            try:
                cell = sh.range(addr)
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

        wb.close()
    finally:
        app.quit()

if __name__ == "__main__":
    main()
