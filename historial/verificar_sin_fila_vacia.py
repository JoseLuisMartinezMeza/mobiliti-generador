import xlwings as xw
import time
from PIL import ImageGrab

app = xw.App(visible=True)
app.api.Visible = False
time.sleep(1)

wb = app.books.open(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_v5_Cotizacion.xlsx")
ws = wb.sheets['Cotizacion']

# Verificar filas 68-72 (ultimo producto + totales)
for r in range(68, 76):
    val_a = ws.range(f'A{r}').value
    val_d = ws.range(f'D{r}').value
    print(f"Fila {r}: A='{val_a}', D='{val_d}'")

# Screenshot de la transicion productos -> totales
ws.api.Range("A65:J75").CopyPicture(Appearance=2, Format=-4147)
ws_temp = wb.sheets.add()
ws_temp.api.Paste()
ws_temp.pictures[0].api.Copy()

img = ImageGrab.grabclipboard()
if img:
    img.save(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_sin_fila_vacia.png")
    print("\nScreenshot guardado")

ws_temp.delete()
wb.close()
app.quit()
