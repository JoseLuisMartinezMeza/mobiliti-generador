import xlwings as xw
import time

app = xw.App(visible=True)
app.api.Visible = False
time.sleep(1)

wb = app.books.open(r"C:\Users\pepem\Downloads\ARMADO DE CARATULA\TEST_KIVO_v6_Cotizacion.xlsx")
ws = wb.sheets['Cotizacion']

# Verificar las celdas G en las primeras filas de producto
for r in range(18, 25):
    val = ws.range(f'G{r}').value
    formula = ws.range(f'G{r}').formula
    print(f"G{r}: valor={val}, formula='{formula}'")

wb.close()
app.quit()
