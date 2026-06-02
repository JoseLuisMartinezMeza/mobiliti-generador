import xlwings as xw
import time

app = xw.App(visible=True)
app.api.Visible = False
time.sleep(1)

wb = app.books.open(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_v3_Cotizacion.xlsx")
ws = wb.sheets['Cotizacion']

# Calcular la posicion Y exacta de cada fila
print("Posicion Y de cada fila (aprox):")
for r in [16, 17, 18, 30, 40, 43, 44, 45, 46, 47, 48]:
    top = ws.range(f'A{r}').top
    height = ws.api.Rows(r).RowHeight
    print(f"  Fila {r}: top={top:.1f}, height={height:.1f}")

print()
print("Imagenes y su posicion Y:")
for i, pic in enumerate(ws.pictures):
    # Encontrar la fila exacta comparando top de cada fila
    for r in range(16, 60):
        cell_top = ws.range(f'A{r}').top
        if abs(pic.top - cell_top) < 5:
            print(f"  Imagen {i+1}: top={pic.top:.1f} -> FILA {r}, tamaño={pic.width:.1f}x{pic.height:.1f}")
            break
    else:
        print(f"  Imagen {i+1}: top={pic.top:.1f} -> (no coincide exacta), tamaño={pic.width:.1f}x{pic.height:.1f}")

print()
print("Celda B45:")
cell = ws.range('B45')
print(f"  width={cell.width:.1f}, height={cell.height:.1f}")
print(f"  top={cell.top:.1f}, left={cell.left:.1f}")

# Que imagen esta en la fila 45?
print()
for i, pic in enumerate(ws.pictures):
    cell_top_45 = ws.range('A45').top
    if abs(pic.top - cell_top_45) < 10:
        print(f">>> IMAGEN EN FILA 45: Imagen #{i+1}")
        print(f"    Tamaño: {pic.width:.1f} x {pic.height:.1f}")
        print(f"    Posicion: top={pic.top:.1f}, left={pic.left:.1f}")
        print(f"    vs celda B45: width={cell.width:.1f}, height={cell.height:.1f}")

wb.close()
app.quit()
