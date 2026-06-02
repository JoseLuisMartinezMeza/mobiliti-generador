import xlwings as xw
import time

app = xw.App(visible=True)
app.api.Visible = False
time.sleep(1)

wb = app.books.open(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_v6_Cotizacion.xlsx")
ws = wb.sheets['Cotizacion']

# Verificar TODAS las filas desde 16 hasta 70
print("Todas las filas G (solo las que tienen formula o valor):")
for r in range(16, 71):
    val = ws.range(f'G{r}').value
    formula = ws.range(f'G{r}').formula
    a_val = ws.range(f'A{r}').value
    if val is not None or formula:
        print(f"Fila {r}: A='{a_val}', G valor={val}, formula='{formula}'")

wb.close()
app.quit()
