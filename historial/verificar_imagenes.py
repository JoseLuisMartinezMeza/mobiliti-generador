import xlwings as xw
import time
from PIL import ImageGrab

app = xw.App(visible=True)
app.api.Visible = False
time.sleep(1)

wb = app.books.open(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_v3_Cotizacion.xlsx")
time.sleep(2)

ws = wb.sheets['Cotizacion']
ws.activate()
time.sleep(1)

# Capturar una seccion con productos e imagenes (filas 20-45)
ws.api.Range("A18:J50").CopyPicture(Appearance=2, Format=-4147)
ws_temp = wb.sheets.add()
ws_temp.api.Paste()
ws_temp.pictures[0].api.Copy()

img = ImageGrab.grabclipboard()
if img:
    img.save(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_imagenes.png")
    print("Screenshot guardado")

ws_temp.delete()
wb.close()
app.quit()
