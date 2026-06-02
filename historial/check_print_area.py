import xlwings as xw
import time

app = xw.App(visible=True)
app.api.Visible = False
time.sleep(1)

wb = app.books.open(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_Cotizacion.xlsx")
time.sleep(1)

ws = wb.sheets['Cotizacion']

# Obtener el área de impresión actual
print_area = ws.api.PageSetup.PrintArea
print(f"PrintArea actual: '{print_area}'")

# Obtener la última fila usada
last_row = ws.api.UsedRange.Rows.Count + ws.api.UsedRange.Row - 1
print(f"Ultima fila usada: {last_row}")

# Verificar si hay filas vacías al final
print(f"Valor A{last_row}: '{ws.range(f'A{last_row}').value}'")
print(f"Valor J{last_row}: '{ws.range(f'J{last_row}').value}'")

# Buscar "Nombre" y "Firma" en las últimas filas
for r in range(last_row - 10, last_row + 1):
    val = ws.range(f'A{r}').value
    if val and ('Nombre' in str(val) or 'Firma' in str(val)):
        print(f"  Fila {r}: '{val}'")

wb.close()
app.quit()
