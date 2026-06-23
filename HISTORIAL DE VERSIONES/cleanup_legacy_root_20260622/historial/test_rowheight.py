import xlwings as xw
import time

app = xw.App(visible=True)
app.api.Visible = False
time.sleep(1)

wb = app.books.open(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\Formato Cotización 2026 GDL (1).xlsx")
ws = wb.sheets['Cotizacion']

h = ws.api.Rows(18).RowHeight
print(f"Tipo: {type(h)}, Valor: {h}")
print(f"Float: {float(h)}, x1.8: {float(h) * 1.8}")

# Probar asignar
ws.api.Rows(20).RowHeight = float(h)
print("Asignacion normal OK")

ws.api.Rows(21).RowHeight = float(h) * 1.8
print("Asignacion x1.8 OK")

wb.close()
app.quit()
