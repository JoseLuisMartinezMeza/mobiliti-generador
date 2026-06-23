import xlwings as xw
import time

app = xw.App(visible=True)
app.api.Visible = False
time.sleep(1)

wb = app.books.open(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_v3_Cotizacion.xlsx")
ws = wb.sheets['Cotizacion']

# Verificar todas las imagenes en la hoja
print(f"Total de imagenes en Cotizacion: {len(ws.pictures)}")
print()

for i, pic in enumerate(ws.pictures):
    print(f"Imagen {i+1}:")
    print(f"  Posicion: top={pic.top:.1f}, left={pic.left:.1f}")
    print(f"  Tamaño: {pic.width:.1f} x {pic.height:.1f}")
    
    # Determinar en qué fila está (aproximado)
    row_approx = int((pic.top - ws.range('A1').top) / ws.api.Rows(1).RowHeight) + 1
    print(f"  Fila aproximada: {row_approx}")
    print()

# Especificamente fila 45 - verificar celda B45
cell = ws.range('B45')
print(f"Celda B45:")
print(f"  width={cell.width:.1f}, height={cell.height:.1f}")
print(f"  Valor en A45: '{ws.range('A45').value}'")
print(f"  Valor en C45: '{ws.range('C45').value}'")

# Buscar imagen cerca de la fila 45
for i, pic in enumerate(ws.pictures):
    row_approx = int((pic.top - ws.range('A1').top) / ws.api.Rows(1).RowHeight) + 1
    if 40 <= row_approx <= 50:
        print(f"\n  >>> Imagen cerca fila 45: #{i+1}, tamaño={pic.width:.1f}x{pic.height:.1f}")

wb.close()
app.quit()
