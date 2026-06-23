Attribute VB_Name = "CotizacionMacro"
' =============================================================================
' MODULO VBA: Generador de Cotizaciones Mobiliti
' =============================================================================
' Importar este modulo en Excel: Alt+F11 > Insertar > Modulo
' Luego ejecutar desde Excel: Alt+F8 > GenerarCotizacion
' =============================================================================

Sub GenerarCotizacion()
    Dim wbTemplate As Workbook
    Dim wbSource As Workbook
    Dim wsQuotation As Worksheet
    Dim wsCotizacion As Worksheet
    Dim wsMobiliti As Worksheet
    Dim fd As Office.FileDialog
    Dim sourcePath As String
    Dim outputPath As String
    Dim lastRow As Long
    Dim i As Long
    Dim currentRow As Long
    Dim itemNo As Variant
    Dim itemName As String
    Dim items() As Variant
    Dim itemCount As Long
    Dim sectionNum As Integer
    Dim sectionStartRow As Long
    Dim mobilitiRowMap As Object
    Dim descuentoRow As Long
    Dim firstDataRow As Long
    Dim lastDataRow As Long
    Dim terminosStartRow As Long
    Dim filasEjemplo As Long
    Dim filasExtra As Long
    Dim rowSub As Long
    Dim rowFlete As Long
    Dim rowSub2 As Long
    Dim rowIva As Long
    Dim rowTotal As Long
    Dim qSheetName As String
    Dim mobRow As Variant
    Dim qRow As Long
    Dim r As Range
    Dim col As Integer
    
    Application.ScreenUpdating = False
    Application.DisplayAlerts = False
    Application.EnableEvents = False
    
    On Error GoTo ErrorHandler
    
    ' -------------------------------------------------------------------------
    ' 1. SELECCIONAR ARCHIVO FUENTE
    ' -------------------------------------------------------------------------
    Set fd = Application.FileDialog(msoFileDialogFilePicker)
    With fd
        .AllowMultiSelect = False
        .Title = "Seleccione el archivo Quotation del proveedor"
        .Filters.Clear
        .Filters.Add "Excel Files", "*.xlsx"
        If .Show = -1 Then
            sourcePath = .SelectedItems(1)
        Else
            GoTo CleanUp
        End If
    End With
    
    ' -------------------------------------------------------------------------
    ' 2. CONFIGURAR OUTPUT
    ' -------------------------------------------------------------------------
    outputPath = Application.ActiveWorkbook.Path & "\Cotizacion_Generada_" & Format(Now, "yyyymmdd_hhmmss") & ".xlsx"
    
    ' -------------------------------------------------------------------------
    ' 3. ABRIR SOURCE
    ' -------------------------------------------------------------------------
    Set wbSource = Workbooks.Open(sourcePath)
    Set wsQuotation = wbSource.Sheets("Quotation")
    
    ' -------------------------------------------------------------------------
    ' 4. COPIAR QUOTATION AL TEMPLATE ACTUAL
    ' -------------------------------------------------------------------------
    Set wbTemplate = ThisWorkbook
    wsQuotation.Copy After:=wbTemplate.Sheets(wbTemplate.Sheets.Count)
    qSheetName = wbTemplate.Sheets(wbTemplate.Sheets.Count).Name
    
    ' -------------------------------------------------------------------------
    ' 5. LEER ITEMS
    ' -------------------------------------------------------------------------
    lastRow = wsQuotation.Cells(wsQuotation.Rows.Count, 1).End(xlUp).Row
    itemCount = 0
    ReDim items(1 To 1000, 1 To 3)
    
    For i = 8 To lastRow
        itemNo = wsQuotation.Cells(i, 1).Value
        itemName = Trim(CStr(Nz(wsQuotation.Cells(i, 2).Value, "")))
        
        ' Categoria: no_val empieza con "-"
        If IsString(itemNo) And Left(itemNo, 1) = "-" Then
            itemCount = itemCount + 1
            items(itemCount, 1) = "categoria"
            items(itemCount, 2) = i
            items(itemCount, 3) = Trim(Replace(Replace(itemNo, "-", ""), "_", ""))
        ' Saltar filas vacias
        ElseIf (IsEmpty(itemName) Or itemName = "") And (IsEmpty(itemNo) Or itemNo = "") Then
            ' Skip
        ' Producto: no_val es numerico
        ElseIf IsNumeric(itemNo) Then
            itemCount = itemCount + 1
            items(itemCount, 1) = "producto"
            items(itemCount, 2) = i
            items(itemCount, 3) = itemName
        ' Categoria sin guion
        ElseIf (IsEmpty(itemNo) Or itemNo = "") And itemName <> "" Then
            itemCount = itemCount + 1
            items(itemCount, 1) = "categoria"
            items(itemCount, 2) = i
            items(itemCount, 3) = itemName
        End If
    Next i
    
    ' -------------------------------------------------------------------------
    ' 6. LLENAR ENCABEZADO
    ' -------------------------------------------------------------------------
    Set wsCotizacion = wbTemplate.Sheets("Cotizacion")
    
    On Error Resume Next
    For i = 3 To 12
        wsCotizacion.Range("A" & i & ":J" & i).UnMerge
    Next i
    On Error GoTo ErrorHandler
    
    wsCotizacion.Range("B3").Value = InputBox("Numero de cotizacion:", "Cotizacion", "100-00000")
    wsCotizacion.Range("B4").Value = Date
    wsCotizacion.Range("B7").Value = InputBox("Proyecto:", "Proyecto", "")
    wsCotizacion.Range("B8").Value = InputBox("Cliente:", "Cliente", "")
    wsCotizacion.Range("B9").Value = InputBox("Correo:", "Correo", "")
    wsCotizacion.Range("B10").Value = InputBox("Telefono:", "Telefono", "")
    wsCotizacion.Range("B11").Value = InputBox("Direccion:", "Direccion", "")
    wsCotizacion.Range("B12").Value = InputBox("Razon Social:", "Razon Social", "")
    
    ' -------------------------------------------------------------------------
    ' 7. GENERAR MOBILITI
    ' -------------------------------------------------------------------------
    Set wsMobiliti = wbTemplate.Sheets("Mobiliti")
    
    lastRow = wsMobiliti.Cells(wsMobiliti.Rows.Count, 1).End(xlUp).Row
    If lastRow < 17 Then lastRow = 200
    
    On Error Resume Next
    For i = 17 To lastRow
        wsMobiliti.Range("A" & i & ":G" & i).UnMerge
    Next i
    On Error GoTo ErrorHandler
    
    wsMobiliti.Range("A17:G" & lastRow).ClearContents
    
    currentRow = 17
    sectionNum = 1
    sectionStartRow = 17
    Set mobilitiRowMap = CreateObject("Scripting.Dictionary")
    
    For i = 1 To itemCount
        If items(i, 1) = "categoria" Then
            If currentRow > sectionStartRow Then
                wsMobiliti.Cells(currentRow, 1).Value = "Subtotales Seccion " & sectionNum
                wsMobiliti.Cells(currentRow, 7).Value = "=SUM(G" & sectionStartRow & ":G" & currentRow - 1 & ")"
                wsMobiliti.Cells(currentRow, 1).Font.Bold = True
                wsMobiliti.Cells(currentRow, 7).Font.Bold = True
                currentRow = currentRow + 1
                sectionNum = sectionNum + 1
            End If
            
            wsMobiliti.Cells(currentRow, 1).Value = "=" & qSheetName & "!A" & items(i, 2)
            Set r = wsMobiliti.Range("A" & currentRow & ":G" & currentRow)
            r.Interior.Color = RGB(62, 37, 0)
            r.Font.Name = "Calibri"
            r.Font.Size = 20
            r.Font.Bold = True
            r.Font.Color = RGB(255, 255, 255)
            
            currentRow = currentRow + 1
            sectionStartRow = currentRow
        Else
            qRow = items(i, 2)
            wsMobiliti.Cells(currentRow, 1).Value = "=" & qSheetName & "!B" & qRow
            wsMobiliti.Cells(currentRow, 2).Value = "=" & qSheetName & "!D" & qRow
            wsMobiliti.Cells(currentRow, 3).Value = "Sunon Inc"
            wsMobiliti.Cells(currentRow, 4).Value = "=IFERROR(VLOOKUP(C" & currentRow & ",Proveedores!A:B,2,0),"" "")"
            wsMobiliti.Cells(currentRow, 5).Value = "=" & qSheetName & "!G" & qRow
            wsMobiliti.Cells(currentRow, 6).Value = "=" & qSheetName & "!J" & qRow
            wsMobiliti.Cells(currentRow, 7).Value = "=E" & currentRow & "*F" & currentRow
            
            Set r = wsMobiliti.Range("A" & currentRow & ":G" & currentRow)
            r.Interior.Color = RGB(255, 192, 0)
            r.Font.Name = "Century Gothic"
            r.Font.Size = 11
            wsMobiliti.Cells(currentRow, 1).Font.Bold = True
            wsMobiliti.Cells(currentRow, 2).Font.Bold = True
            wsMobiliti.Cells(currentRow, 3).Font.Bold = False
            wsMobiliti.Cells(currentRow, 4).Font.Bold = True
            wsMobiliti.Cells(currentRow, 5).Font.Bold = True
            wsMobiliti.Cells(currentRow, 6).Font.Bold = False
            wsMobiliti.Cells(currentRow, 7).Font.Bold = False
            
            mobilitiRowMap(qRow) = currentRow
            currentRow = currentRow + 1
        End If
    Next i
    
    If currentRow > sectionStartRow Then
        wsMobiliti.Cells(currentRow, 1).Value = "Subtotales Seccion " & sectionNum
        wsMobiliti.Cells(currentRow, 7).Value = "=SUM(G" & sectionStartRow & ":G" & currentRow - 1 & ")"
        wsMobiliti.Cells(currentRow, 1).Font.Bold = True
        wsMobiliti.Cells(currentRow, 7).Font.Bold = True
    End If
    
    ' -------------------------------------------------------------------------
    ' 8. GENERAR COTIZACION
    ' -------------------------------------------------------------------------
    terminosStartRow = 0
    For i = 16 To 100
        If InStr(1, CStr(Nz(wsCotizacion.Cells(i, 1).Value, "")), "CONDICIONES") > 0 Then
            terminosStartRow = i
            Exit For
        End If
    Next i
    If terminosStartRow = 0 Then terminosStartRow = 32
    
    On Error Resume Next
    For i = 16 To terminosStartRow + 30
        wsCotizacion.Range("A" & i & ":J" & i).UnMerge
    Next i
    On Error GoTo ErrorHandler
    
    For i = 16 To terminosStartRow - 1
        wsCotizacion.Range("A" & i & ":J" & i).ClearContents
    Next i
    
    filasEjemplo = terminosStartRow - 16
    If itemCount > filasEjemplo Then
        filasExtra = itemCount - filasEjemplo
        wsCotizacion.Rows(terminosStartRow & ":" & terminosStartRow + filasExtra - 1).Insert _
            Shift:=xlDown, CopyOrigin:=xlFormatFromLeftOrAbove
        terminosStartRow = terminosStartRow + filasExtra
    End If
    
    currentRow = 16
    firstDataRow = 0
    descuentoRow = 0
    
    For i = 1 To itemCount
        If items(i, 1) = "categoria" Then
            wsCotizacion.Cells(currentRow, 1).Value = "=" & qSheetName & "!A" & items(i, 2)
            Set r = wsCotizacion.Range("A" & currentRow & ":J" & currentRow)
            r.Interior.Color = RGB(115, 169, 219)
            r.Font.Name = "Roboto"
            r.Font.Size = 16
            r.Font.Bold = True
            r.Font.Color = RGB(0, 0, 0)
            r.Merge
            currentRow = currentRow + 1
        Else
            If firstDataRow = 0 Then
                firstDataRow = currentRow
                descuentoRow = currentRow + 2
            End If
            
            qRow = items(i, 2)
            mobRow = mobilitiRowMap(qRow)
            
            wsCotizacion.Cells(currentRow, 1).Value = "=" & qSheetName & "!B" & qRow
            wsCotizacion.Cells(currentRow, 3).Value = "=" & qSheetName & "!D" & qRow
            wsCotizacion.Cells(currentRow, 4).Value = "=" & qSheetName & "!E" & qRow
            wsCotizacion.Cells(currentRow, 5).Value = "=" & qSheetName & "!G" & qRow
            
            If Not IsEmpty(mobRow) Then
                wsCotizacion.Cells(currentRow, 6).Value = "=Mobiliti!F" & mobRow
            Else
                wsCotizacion.Cells(currentRow, 6).Value = "=" & qSheetName & "!J" & qRow
            End If
            
            wsCotizacion.Cells(currentRow, 7).Value = "=G$" & descuentoRow
            wsCotizacion.Cells(currentRow, 8).Value = "=F" & currentRow & "*G" & currentRow
            wsCotizacion.Cells(currentRow, 9).Value = "=F" & currentRow & "-H" & currentRow
            wsCotizacion.Cells(currentRow, 10).Value = "=I" & currentRow & "*E" & currentRow
            
            Set r = wsCotizacion.Range("A" & currentRow & ":J" & currentRow)
            r.Interior.ColorIndex = 0
            wsCotizacion.Cells(currentRow, 1).Font.Name = "Roboto"
            wsCotizacion.Cells(currentRow, 1).Font.Size = 16
            wsCotizacion.Cells(currentRow, 1).Font.Bold = True
            wsCotizacion.Cells(currentRow, 2).Font.Name = "Roboto"
            wsCotizacion.Cells(currentRow, 2).Font.Size = 16
            wsCotizacion.Cells(currentRow, 2).Font.Bold = False
            wsCotizacion.Cells(currentRow, 3).Font.Name = "Roboto"
            wsCotizacion.Cells(currentRow, 3).Font.Size = 16
            wsCotizacion.Cells(currentRow, 3).Font.Bold = False
            For col = 4 To 10
                wsCotizacion.Cells(currentRow, col).Font.Name = "Roboto"
                wsCotizacion.Cells(currentRow, col).Font.Size = 16
                wsCotizacion.Cells(currentRow, col).Font.Bold = True
            Next col
            
            currentRow = currentRow + 1
        End If
    Next i
    
    lastDataRow = currentRow - 1
    
    If descuentoRow > 0 And descuentoRow <= lastDataRow Then
        wsCotizacion.Cells(descuentoRow, 7).Value = 0.7
    End If
    
    ' Totales
    rowSub = terminosStartRow - 5
    If rowSub <= lastDataRow + 1 Then
        wsCotizacion.Rows(lastDataRow + 2 & ":" & lastDataRow + 6).Insert _
            Shift:=xlDown, CopyOrigin:=xlFormatFromLeftOrAbove
        rowSub = lastDataRow + 2
        terminosStartRow = rowSub + 5
    End If
    
    On Error Resume Next
    For i = rowSub To rowSub + 4
        wsCotizacion.Range("A" & i & ":J" & i).UnMerge
    Next i
    On Error GoTo ErrorHandler
    
    wsCotizacion.Cells(rowSub, 4).Value = "SUBTOTAL:"
    wsCotizacion.Cells(rowSub, 7).Value = "=SUM(J" & firstDataRow & ":J" & lastDataRow & ")"
    wsCotizacion.Cells(rowSub, 4).Font.Bold = True
    wsCotizacion.Cells(rowSub, 7).Font.Bold = True
    
    rowFlete = rowSub + 1
    wsCotizacion.Cells(rowFlete, 4).Value = "COSTO DE FLETE E INSTALACION:"
    wsCotizacion.Cells(rowFlete, 7).Value = "=G" & rowSub & "*12%"
    wsCotizacion.Cells(rowFlete, 4).Font.Bold = True
    wsCotizacion.Cells(rowFlete, 7).Font.Bold = True
    
    rowSub2 = rowFlete + 1
    wsCotizacion.Cells(rowSub2, 4).Value = "SUBTOTAL:"
    wsCotizacion.Cells(rowSub2, 7).Value = "=G" & rowSub & "+G" & rowFlete
    wsCotizacion.Cells(rowSub2, 4).Font.Bold = True
    wsCotizacion.Cells(rowSub2, 7).Font.Bold = True
    
    rowIva = rowSub2 + 1
    wsCotizacion.Cells(rowIva, 4).Value = "IVA:"
    wsCotizacion.Cells(rowIva, 7).Value = "=G" & rowSub2 & "*16%"
    wsCotizacion.Cells(rowIva, 4).Font.Bold = True
    wsCotizacion.Cells(rowIva, 7).Font.Bold = True
    
    rowTotal = rowIva + 1
    wsCotizacion.Cells(rowTotal, 4).Value = "TOTAL:"
    wsCotizacion.Cells(rowTotal, 7).Value = "=G" & rowSub2 & "+G" & rowIva
    wsCotizacion.Cells(rowTotal, 4).Font.Bold = True
    wsCotizacion.Cells(rowTotal, 7).Font.Bold = True
    
    ' Guardar
    wbTemplate.SaveAs outputPath
    wbSource.Close SaveChanges:=False
    wbTemplate.Close
    
    MsgBox "Cotizacion generada exitosamente!" & vbCrLf & vbCrLf & outputPath, vbInformation
    
CleanUp:
    Application.ScreenUpdating = True
    Application.DisplayAlerts = True
    Application.EnableEvents = True
    Exit Sub
    
ErrorHandler:
    MsgBox "Error: " & Err.Number & " - " & Err.Description, vbCritical
    Resume CleanUp
End Sub

' Helper para evitar errores con valores nulos
Function Nz(value, defaultValue)
    If IsEmpty(value) Or IsNull(value) Then
        Nz = defaultValue
    Else
        Nz = value
    End If
End Function

' Helper para verificar si es string
Function IsString(value)
    IsString = (VarType(value) = vbString)
End Function
