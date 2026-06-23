import xlwings as xw
import time

app = xw.App(visible=True)
app.api.Visible = False
time.sleep(1)

wb = app.books.open(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_Cotizacion.xlsx")
time.sleep(2)

# Screenshot de la hoja Cotizacion (ultimos productos + totales + terminos)
ws = wb.sheets['Cotizacion']
ws.activate()
time.sleep(1)

# Capturar la seccion de totales y terminos (aprox filas 65-90)
ws.api.Range("A65:J100").Select()
time.sleep(1)

# Tomar screenshot de la seleccion
img_path = r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_verificacion.png"
app.api.ActiveWindow.DisplayHeadings = False
app.api.ActiveWindow.DisplayGridlines = False

# Copiar como imagen
ws.api.Range("A65:J100").CopyPicture(Appearance=2, Format=-4147)
# Pegar en nueva hoja temporal para exportar
ws_temp = wb.sheets.add()
ws_temp.api.Paste()
ws_temp.pictures[0].api.Copy()

# Guardar con PIL
from PIL import ImageGrab
img = ImageGrab.grabclipboard()
if img:
    img.save(img_path)
    print(f"Screenshot guardado: {img_path}")
else:
    print("No se pudo capturar imagen")

ws_temp.delete()

wb.close(save=False)
app.quit()
print("Verificacion completada")
