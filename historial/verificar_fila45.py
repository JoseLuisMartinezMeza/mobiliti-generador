import xlwings as xw
import time

app = xw.App(visible=True)
app.api.Visible = False
time.sleep(1)

wb = app.books.open(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_v4_Cotizacion.xlsx")
ws = wb.sheets['Cotizacion']

# Verificar imagen en fila 45
cell_top_45 = ws.range('A45').top
print(f"Fila 45: top={cell_top_45:.1f}, height={ws.api.Rows(45).RowHeight:.1f}")
print(f"Celda B45: width={ws.range('B45').width:.1f}, height={ws.range('B45').height:.1f}")
print()

for i, pic in enumerate(ws.pictures):
    if abs(pic.top - cell_top_45) < 10:
        print(f">>> IMAGEN EN FILA 45: Imagen #{i+1}")
        print(f"    Tamaño: {pic.width:.1f} x {pic.height:.1f}")
        print(f"    Posicion: top={pic.top:.1f}, left={pic.left:.1f}")
        
        # Verificar que esté centrada
        cell = ws.range('B45')
        expected_left = cell.left + (cell.width - pic.width) / 2
        expected_top = cell.top + (cell.height - pic.height) / 2
        print(f"    Esperado left={expected_left:.1f}, top={expected_top:.1f}")
        print(f"    Actual   left={pic.left:.1f}, top={pic.top:.1f}")
        print(f"    Diferencia: left={abs(pic.left - expected_left):.1f}, top={abs(pic.top - expected_top):.1f}")

# Verificar todas las imagenes tienen tamaños similares
print()
print("Resumen de todas las imagenes:")
sizes = [(i+1, pic.width, pic.height) for i, pic in enumerate(ws.pictures)]
for num, w, h in sizes:
    print(f"  Imagen {num}: {w:.1f} x {h:.1f}")

wb.close()
app.quit()
