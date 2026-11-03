"""
Citrello button for Microsoft Word (Windows).

Install builds a macro-enabled template with Word itself (so the macro is in
Word's own format), adds a "Citrello" ribbon tab to it and puts it in Word's
Startup folder, which Word loads automatically and trusts by default.

Ribbon buttons (all work inside the open document; each is one Undo step):
  Insert / Edit citation  search dialog; with the cursor in a citation, edit it
  Update citations        renumber/reformat citations and the bibliography, make [@Key] markers live
  Citation style          choose the style for this document (kept in the document)
  Open Citrello            open the library in the browser
"""
import datetime
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

import citeformat
import library

PORT = 8585
TEMPLATE_NAME = "Citrello.dotm"

VBA = r'''Option Explicit

' Citrello tab for Word. Installed by the Citrello app; remove it from Citrello's Settings.
' Everything happens inside the open document (one Undo step per action); Citrello only formats.
Private Const CITRELLO_URL As String = "http://127.0.0.1:%(port)d"
Private Const CITRELLO_CMD As String = "%(cmd)s"
Private lastCount As Long
Private lastStyle As String
Private stepName As String   ' what the macro was doing, for error messages
Private lastShown As String  ' the citation whose reference is in the status bar

' ---------------- ribbon buttons ----------------
Public Sub CitrelloInsert(control As IRibbonControl)
    DoInsert
End Sub

Public Sub CitrelloUpdate(control As IRibbonControl)
    DoUpdate False
End Sub

Public Sub CitrelloStyle(control As IRibbonControl)
    DoStyle
End Sub

Public Sub CitrelloOpen(control As IRibbonControl)
    On Error GoTo Fail
    EnsureCitrello
    ThisDocument.FollowHyperlink CITRELLO_URL & "/"
    Exit Sub
Fail:
    MsgBox Err.Description, vbExclamation, "Citrello"
End Sub

' ---------------- the same, for the Quick Access Toolbar / Alt+F8 ----------------
Public Sub CitrelloInsertNow()
    DoInsert
End Sub

Public Sub CitrelloUpdateNow()
    DoUpdate False
End Sub

Public Sub CitrelloStyleNow()
    DoStyle
End Sub

' ---------------- Click a citation: show its reference in the status bar ----------------

Public Sub CitrelloShowAt(ByVal Sel As Selection)
    Dim f As Field, rows() As String, i As Long, p() As String, msg As String, info As String, code As String
    On Error GoTo Done
    For Each f In ActiveDocument.Fields
        If IsCitation(f) Then
            If Sel.Start > f.Code.Start - 1 And Sel.Start <= f.Result.End Then
                code = f.Code.Text
                If code = lastShown Then Exit Sub
                lastShown = code
                If Not IsRunning() Then
                    Application.StatusBar = "Citrello: start Citrello to see this reference."
                    Exit Sub
                End If
                rows = Split(TryHttp("POST", "/api/word/items", "{""code"":" & CitrelloJson(code) & "}", msg), vbLf)
                For i = 0 To UBound(rows)
                    p = Split(rows(i), vbTab)
                    If UBound(p) >= 4 Then
                        info = p(1)
                        If Len(p(4)) > 0 Then info = p(4) & " " & info
                        If Len(p(2)) > 0 Then info = info & ", p. " & p(2)
                        If Len(msg) > 0 Then msg = msg & "   |   "
                        msg = msg & info
                    End If
                Next i
                If Len(msg) = 0 Then msg = "this citation is not in your library"
                Application.StatusBar = Left("Citrello: " & f.Result.Text & "  " & msg, 250)
                Exit Sub
            End If
        End If
    Next f
Done:
End Sub

Public Sub CitrelloClearShown()
    If Len(lastShown) > 0 Then
        lastShown = ""
        Application.StatusBar = ""
    End If
End Sub

' double-click a citation: open it in the edit dialog
Public Function CitrelloEditAt(ByVal Sel As Selection) As Boolean
    Dim f As Field
    On Error GoTo Done
    For Each f In ActiveDocument.Fields
        If IsCitation(f) Then
            If Sel.Start > f.Code.Start - 1 And Sel.Start <= f.Result.End Then
                CitrelloEditAt = True
                DoInsert
                Exit Function
            End If
        End If
    Next f
Done:
End Function

' ---------------- Diagnostics: writes what Word sees into a new document ----------------
Public Sub CitrelloDiagnose(control As IRibbonControl)
    CitrelloDiagnoseNow
End Sub

Public Sub CitrelloDiagnoseNow()
    Dim doc As Document, rep As Document, f As Field, n As Long, out As String, codes As String, c As String, k As Long, raw As String
    On Error Resume Next
    If Documents.Count = 0 Then Exit Sub
    Set doc = ActiveDocument
    StartListener
    out = "Citrello diagnostics" & vbCr & "Word " & Application.Version & " (build " & Application.Build & ")" & vbCr & _
          "Click listener active: " & Application.Run("CitrelloListening") & vbCr & _
          "Document: " & doc.Name & " | fields: " & doc.Fields.Count & " | style: " & DocStyle(doc) & vbCr & vbCr
    For Each f In doc.Fields
        n = n + 1
        c = f.Code.Text
        out = out & "#" & n & " type=" & f.Type & " codeLen=" & Len(c) & " citation=" & IsCitation(f) & vbCr & _
              "   code start: " & Left(Replace(c, vbCr, " "), 90) & vbCr & _
              "   code end:   " & Right(Replace(c, vbCr, " "), 50) & vbCr & _
              "   shows: " & Left(Replace(f.Result.Text, vbCr, " | "), 90) & vbCr
        If IsCitation(f) Then
            If k > 0 Then codes = codes & ","
            codes = codes & CitrelloJson(c)
            k = k + 1
        End If
        If Err.Number <> 0 Then
            out = out & "   ERROR " & Err.Number & ": " & Err.Description & vbCr
            Err.Clear
        End If
    Next f
    If IsRunning() Then
        raw = CitrelloHttp("POST", "/api/word/render", "{""style"":" & CitrelloJson(DocStyle(doc)) & ",""codes"":[" & codes & "]}")
        If Err.Number <> 0 Then
            raw = "ERROR " & Err.Number & ": " & Err.Description
            Err.Clear
        End If
        out = out & vbCr & "Citrello's answer (first 1500 characters):" & vbCr & Left(Replace(raw, vbLf, vbCr), 1500)
    Else
        out = out & vbCr & "Citrello is not running."
    End If
    Set rep = Documents.Add
    rep.Content.Text = out
    MsgBox "Diagnostics written to a new document. Copy its text (Ctrl+A, Ctrl+C) and paste it to Claude.", vbInformation, "Citrello"
End Sub

' ---------------- Update citations ----------------
Private Sub DoUpdate(ByVal silent As Boolean)
    Dim doc As Document, ur As UndoRecord, ownRecord As Boolean, track As Boolean, report As String, errText As String
    On Error GoTo Fail
    If Documents.Count = 0 Then Exit Sub
    Set doc = ActiveDocument
    EnsureCitrello
    Set ur = Application.UndoRecord
    If Not ur.IsRecordingCustomRecord Then
        ur.StartCustomRecord "Citrello: update citations"
        ownRecord = True
    End If
    Application.ScreenUpdating = False
    track = doc.TrackRevisions
    doc.TrackRevisions = False
    Application.StatusBar = "Citrello: updating citations..."
    stepName = "converting [@Key] markers"
    report = ConvertMarkers(doc)
    stepName = "removing deleted citations"
    RemoveEmptiedCitations doc
    stepName = "reading the citations"
    report = report & RenderAll(doc)
    doc.TrackRevisions = track
    Application.ScreenUpdating = True
    If ownRecord Then ur.EndCustomRecord
    Application.StatusBar = "Citrello: " & lastCount & " citation(s) updated (" & lastStyle & ")."
    If Len(report) > 0 And Not silent Then MsgBox report, vbInformation, "Citrello"
    Exit Sub
Fail:
    errText = Err.Description & vbCr & vbCr & "(while " & stepName & "; error " & Err.Number & ")"
    On Error Resume Next
    doc.TrackRevisions = track
    Application.ScreenUpdating = True
    If ownRecord Then ur.EndCustomRecord
    Application.StatusBar = ""
    MsgBox errText, vbExclamation, "Citrello"
End Sub

' A new ADDIN field has no result part (its "Result" is the spot just after the field), so text written there
' ends up outside the field. A QUOTE field gets a real result straight away; then its code is swapped for ours.
' (Fields.Add also cuts long field text at ~255 characters, another reason to set the code afterwards.)
Private Function NewAddinField(doc As Document, rng As Range, ByVal code As String, ByVal shown As String) As Field
    Dim f As Field
    Set f = doc.Fields.Add(rng, wdFieldQuote, """" & Replace(shown, """", "'") & """", False)
    f.Code.Text = " " & code & " "
    Set NewAddinField = f
End Function

Private Function NewCitationField(doc As Document, rng As Range, ByVal code As String, ByVal shown As String) As Field
    Set NewCitationField = NewAddinField(doc, rng, code, shown)
End Function

Private Function IsWordChar(ByVal c As String) As Boolean
    If Len(c) = 0 Then Exit Function
    IsWordChar = (c Like "[A-Za-z0-9]") Or (AscW(c) And &HFFFF&) > 191
End Function

' a citation never goes inside a word: "signi|ficant" -> "significant|"
Private Sub AvoidSplittingWord(doc As Document, rng As Range)
    Dim pos As Long, lastPos As Long
    pos = rng.Start
    lastPos = doc.Content.End - 1
    If pos = 0 Or pos >= lastPos Then Exit Sub
    If Not (IsWordChar(doc.Range(pos - 1, pos).Text) And IsWordChar(doc.Range(pos, pos + 1).Text)) Then Exit Sub
    Do While pos < lastPos
        If Not IsWordChar(doc.Range(pos, pos + 1).Text) Then Exit Do
        pos = pos + 1
    Loop
    rng.SetRange pos, pos
End Sub

Private Function IsCitation(f As Field) As Boolean
    Dim c As String
    c = f.Code.Text
    IsCitation = (InStr(c, "ADDIN") > 0 And InStr(c, "CSL_CITATION") > 0)
End Function

' [@Key] markers (typed, or written by Claude) -> citation fields
Private Function ConvertMarkers(doc As Document) As String
    Dim r As Range, f As Field, pos As Long, txt As String, code As String, msg As String
    pos = doc.Content.Start
    Do
        Set r = doc.Range(pos, doc.Content.End)
        With r.Find
            .ClearFormatting
            .Text = "\[*\]"
            .MatchWildcards = True
            .Forward = True
            .Wrap = wdFindStop
            .Format = False
        End With
        If Not r.Find.Execute Then Exit Do
        If r.End <= pos Then Exit Do
        pos = r.End
        txt = r.Text
        If InStr(txt, "@") > 0 And Len(txt) < 400 And InStr(txt, vbCr) = 0 And r.Fields.Count = 0 Then
            code = TryHttp("POST", "/api/word/marker", "{""text"":" & CitrelloJson(txt) & "}", msg)
            If Len(code) > 0 Then
                Set f = NewCitationField(doc, r, code, txt)
                pos = f.Result.End + 1
            Else
                ConvertMarkers = ConvertMarkers & txt & ": " & msg & vbCr
            End If
        End If
    Loop
End Function

' a citation whose visible text was deleted counts as deleted
Private Sub RemoveEmptiedCitations(doc As Document)
    Dim i As Long, f As Field
    For i = doc.Fields.Count To 1 Step -1
        Set f = doc.Fields(i)
        If IsCitation(f) Then
            If Len(Trim(Replace(f.Result.Text, Chr(160), ""))) = 0 Then f.Delete
        End If
    Next i
End Sub

Private Function RenderAll(doc As Document) As String
    Dim cites As New Collection, f As Field, body As String, n As Long, raw As String
    Dim lines() As String, parts() As String, i As Long, bibText As String, bibSpans As String
    Dim hanging As Boolean, entries As Long, offset As Long, hasBib As Boolean, cf As Field, badCount As Long
    For Each f In doc.Fields
        If IsCitation(f) Then
            cites.Add f
            If n > 0 Then body = body & ","
            body = body & CitrelloJson(f.Code.Text)
            n = n + 1
        ElseIf InStr(f.Code.Text, "CSL_BIBLIOGRAPHY") > 0 Or InStr(f.Code.Text, "ZOTERO_BIBL") > 0 Then
            hasBib = True
        End If
    Next f
    lastCount = n
    If n = 0 And Not hasBib Then
        lastStyle = "nothing to update"
        Exit Function
    End If
    stepName = "asking Citrello to format " & n & " citation(s)"
    raw = CitrelloHttp("POST", "/api/word/render", "{""style"":" & CitrelloJson(DocStyle(doc)) & ",""codes"":[" & body & "]}")
    lines = Split(raw, vbLf)
    For i = 0 To UBound(lines)
        parts = Split(lines(i), vbTab)
        If UBound(parts) >= 1 Then
            Select Case parts(0)
                Case "STYLE"
                    lastStyle = parts(1)
                Case "HANGING"
                    hanging = (parts(1) = "1")
                Case "CITE"
                    If UBound(parts) >= 4 Then
                        stepName = "formatting citation " & (CLng(parts(1)) + 1) & " of " & n
                        Set cf = cites(CLng(parts(1)) + 1)
                        SetCitation doc, cf, parts(2), parts(3), parts(4)
                    End If
                Case "BIB"
                    offset = Len(bibText)
                    If entries > 0 Then
                        bibText = bibText & vbCr
                        offset = offset + 1
                    End If
                    bibText = bibText & parts(1)
                    If UBound(parts) >= 2 Then
                        If Len(parts(2)) > 0 Then
                            If Len(bibSpans) > 0 Then bibSpans = bibSpans & ";"
                            bibSpans = bibSpans & ShiftSpans(parts(2), offset)
                        End If
                    End If
                    entries = entries + 1
                Case "REPORT"
                    RenderAll = RenderAll & parts(1) & vbCr
                Case "BAD"
                    badCount = badCount + 1
                    Set cf = cites(CLng(parts(1)) + 1)
                    cf.Result.HighlightColorIndex = wdYellow
            End Select
        End If
    Next i
    stepName = "writing the bibliography"
    WriteBibliography doc, bibText, bibSpans, hanging
    If badCount > 0 Then RenderAll = RenderAll & badCount & " citation(s) could not be read and are highlighted in yellow " & _
        "(probably left over from an earlier failed attempt). Delete them and insert them again." & vbCr
End Function

Private Sub SetCitation(doc As Document, f As Field, ByVal txt As String, ByVal spans As String, ByVal code As String)
    Dim r As Range, prevChar As String, fieldStart As Long
    f.Code.Text = " " & code & " "
    fieldStart = f.Code.Start - 1
    If fieldStart > 0 Then prevChar = doc.Range(fieldStart - 1, fieldStart).Text
    If prevChar = " " Then
        If InStr(spans, ",sup") > 0 Then
            doc.Range(fieldStart - 1, fieldStart).Delete   ' "text 1" -> "text" + superscript 1
        ElseIf Left(txt, 1) = " " Then
            txt = Mid(txt, 2)                          ' "text  [1]" -> "text [1]"
            spans = ShiftSpans(spans, -1)
        End If
    End If
    Set r = f.Result
    r.Text = txt
    Set r = f.Result
    ClearFormatting r
    ApplySpans doc, r, spans
End Sub

' formatting is cosmetic: if Word refuses a property, skip it instead of stopping the update
Private Sub ClearFormatting(r As Range)
    On Error Resume Next
    r.Italic = False
    r.Bold = False
    r.Font.Superscript = False
    r.Font.Subscript = False
    r.Font.SmallCaps = False
End Sub

Private Sub WriteBibliography(doc As Document, ByVal txt As String, ByVal spans As String, ByVal hanging As Boolean)
    Dim f As Field, bf As Field, r As Range, h As Paragraph
    For Each f In doc.Fields
        If InStr(f.Code.Text, "CSL_BIBLIOGRAPHY") > 0 Or InStr(f.Code.Text, "ZOTERO_BIBL") > 0 Then
            Set bf = f
            Exit For
        End If
    Next f
    If bf Is Nothing Then
        If Len(txt) = 0 Then Exit Sub
        Set h = BibHeading(doc)
        If h Is Nothing Then
            doc.Content.InsertParagraphAfter
            doc.Paragraphs.Last.Range.InsertBefore "References"
            doc.Paragraphs.Last.Style = wdStyleHeading1
            Set h = doc.Paragraphs.Last
        End If
        h.Range.InsertParagraphAfter
        Set r = h.Range.Next(wdParagraph, 1)
        r.Style = wdStyleNormal
        r.Collapse wdCollapseStart
        Set bf = NewAddinField(doc, r, "ADDIN Mendeley Bibliography CSL_BIBLIOGRAPHY", "Bibliography")
    End If
    Set r = bf.Result
    r.Text = txt
    Set r = bf.Result
    ClearFormatting r
    ApplySpans doc, r, spans
    On Error Resume Next   ' indentation is cosmetic
    If hanging Then
        r.ParagraphFormat.LeftIndent = 36
        r.ParagraphFormat.FirstLineIndent = -36
    Else
        r.ParagraphFormat.LeftIndent = 0
        r.ParagraphFormat.FirstLineIndent = 0
    End If
End Sub

Private Function BibHeading(doc As Document) As Paragraph
    Dim p As Paragraph, t As String
    For Each p In doc.Paragraphs
        t = LCase(Trim(Replace(Replace(p.Range.Text, vbCr, ""), Chr(7), "")))
        If t = "references" Or t = "bibliography" Or t = "reference list" Or t = "works cited" Or t = "literature cited" Or t = "sources" Then
            Set BibHeading = p
        End If
    Next p
End Function

Private Sub ApplySpans(doc As Document, r As Range, ByVal spans As String)
    Dim parts() As String, p() As String, i As Long, part As Range
    If Len(spans) = 0 Then Exit Sub
    On Error Resume Next   ' formatting is cosmetic
    parts = Split(spans, ";")
    For i = 0 To UBound(parts)
        p = Split(parts(i), ",")
        If UBound(p) = 2 Then
            Set part = doc.Range(r.Start + CLng(p(0)), r.Start + CLng(p(1)))
            Select Case p(2)
                Case "i": part.Italic = True
                Case "b": part.Bold = True
                Case "sup": part.Font.Superscript = True
                Case "sub": part.Font.Subscript = True
                Case "sc": part.Font.SmallCaps = True
            End Select
        End If
    Next i
End Sub

Private Function ShiftSpans(ByVal spans As String, ByVal delta As Long) As String
    Dim parts() As String, p() As String, i As Long, a As Long, b As Long, out As String
    If Len(spans) = 0 Then Exit Function
    parts = Split(spans, ";")
    For i = 0 To UBound(parts)
        p = Split(parts(i), ",")
        If UBound(p) = 2 Then
            a = CLng(p(0)) + delta
            b = CLng(p(1)) + delta
            If a < 0 Then a = 0
            If b > a Then
                If Len(out) > 0 Then out = out & ";"
                out = out & a & "," & b & "," & p(2)
            End If
        End If
    Next i
    ShiftSpans = out
End Function

' ---------------- Insert / edit citation ----------------
Private Sub DoInsert()
    Dim frm As Object, editField As Field, itemsJson As String, code As String, rng As Range, f As Field
    Dim ur As UndoRecord, prevChar As String, errText As String, recording As Boolean, insPos As Long, doc As Document
    On Error GoTo Fail
    If Documents.Count = 0 Then Exit Sub
    EnsureCitrello
    Set doc = ActiveDocument
    insPos = Selection.End        ' remember where the cursor is before the dialog opens
    stepName = "looking for a citation at the cursor"
    Set editField = CitationAtSelection()
    On Error Resume Next
    Set frm = VBA.UserForms.Add("CitrelloCite")
    On Error GoTo Fail
    If frm Is Nothing Then
        itemsJson = PickWithInputBox()        ' manual install without the dialog
    Else
        If Not editField Is Nothing Then frm.Prefill CitrelloHttp("POST", "/api/word/items", "{""code"":" & CitrelloJson(editField.Code.Text) & "}")
        frm.Show
        If Not frm.Cancelled Then itemsJson = frm.ResultJson
        Unload frm
    End If
    If Len(itemsJson) = 0 Then Exit Sub
    stepName = "creating the citation"
    code = CitrelloHttp("POST", "/api/word/cite", "{""items"":" & itemsJson & "}")
    Set ur = Application.UndoRecord
    If editField Is Nothing Then
        ur.StartCustomRecord "Citrello: insert citation"
    Else
        ur.StartCustomRecord "Citrello: edit citation"
    End If
    recording = True
    If editField Is Nothing Then
        Set rng = doc.Range(insPos, insPos)
        AvoidSplittingWord doc, rng
        If rng.Start > 0 Then
            prevChar = doc.Range(rng.Start - 1, rng.Start).Text
            If prevChar <> " " And prevChar <> "(" And prevChar <> vbCr And prevChar <> Chr(160) And prevChar <> vbTab Then
                rng.InsertAfter " "
                rng.Collapse wdCollapseEnd
            End If
        End If
        stepName = "inserting the citation field"
        Set f = NewCitationField(ActiveDocument, rng, code, "[...]")
    Else
        Set f = editField
        f.Code.Text = " " & code & " "
    End If
    DoUpdate False
    ur.EndCustomRecord
    recording = False
    On Error Resume Next   ' just placing the cursor after the citation
    Selection.SetRange f.Result.End + 1, f.Result.End + 1
    Exit Sub
Fail:
    errText = Err.Description & vbCr & vbCr & "(while " & stepName & "; error " & Err.Number & ")"
    On Error Resume Next
    If recording Then ur.EndCustomRecord
    MsgBox errText, vbExclamation, "Citrello"
End Sub

Private Function CitationAtSelection() As Field
    Dim f As Field, s As Long
    s = Selection.Start
    For Each f In ActiveDocument.Fields
        If IsCitation(f) Then
            If s > f.Code.Start - 1 And s <= f.Result.End Then
                Set CitationAtSelection = f
                Exit Function
            End If
        End If
    Next f
End Function

Private Function PickWithInputBox() As String
    Dim q As String, raw As String, hits() As String, i As Long, menu As String, answer As String, parts() As String
    Dim n As Long, out As String, itemText As String, pg As String
    q = InputBox("Search your Citrello library (author, title, year or key):", "Citrello: insert citation")
    If StrPtr(q) = 0 Then Exit Function
    raw = CitrelloHttp("GET", "/api/library/search.txt?q=" & CitrelloUrlEncode(Trim(q)), "")
    If Len(raw) = 0 Then
        MsgBox "Nothing found.", vbInformation, "Citrello"
        Exit Function
    End If
    hits = Split(raw, vbLf)
    For i = 0 To UBound(hits)
        menu = menu & (i + 1) & ". " & Mid(hits(i), InStr(hits(i), "|") + 1) & vbCr
    Next i
    answer = Trim(InputBox(menu & vbCr & "Number(s) to cite, e.g. 1 or 1,3. Page: 2:15", "Citrello: insert citation", "1"))
    If answer = "" Then Exit Function
    parts = Split(Replace(answer, ";", ","), ",")
    For i = 0 To UBound(parts)
        itemText = Trim(parts(i))
        pg = ""
        If InStr(itemText, ":") > 0 Then
            pg = Trim(Mid(itemText, InStr(itemText, ":") + 1))
            itemText = Trim(Left(itemText, InStr(itemText, ":") - 1))
        End If
        If IsNumeric(itemText) Then
            n = CLng(itemText) - 1
            If n >= 0 And n <= UBound(hits) Then
                If Len(out) > 0 Then out = out & ","
                out = out & "{""key"":" & CitrelloJson(Left(hits(n), InStr(hits(n), "|") - 1)) & ",""locator"":" & CitrelloJson(pg) & "}"
            End If
        End If
    Next i
    If Len(out) > 0 Then PickWithInputBox = "[" & out & "]"
End Function

' ---------------- Citation style ----------------
Private Sub DoStyle()
    Dim doc As Document, frm As Object, chosen As String, msg As String
    On Error GoTo Fail
    If Documents.Count = 0 Then Exit Sub
    Set doc = ActiveDocument
    EnsureCitrello
    On Error Resume Next
    Set frm = VBA.UserForms.Add("CitrelloStyleForm")
    On Error GoTo Fail
    If frm Is Nothing Then
        chosen = LCase(Trim(InputBox("Citation style id for this document (e.g. apa, ieee, nature, optica):", "Citrello", DocStyle(doc))))
    Else
        frm.SetCurrent DocStyle(doc)
        frm.Show
        If Not frm.Cancelled Then chosen = frm.Chosen
        Unload frm
    End If
    If Len(chosen) = 0 Then Exit Sub
    If Len(TryHttp("GET", "/api/styles/check?id=" & CitrelloUrlEncode(chosen), "", msg)) = 0 Then
        MsgBox msg, vbExclamation, "Citrello"
        Exit Sub
    End If
    doc.Variables("CitrelloStyle").Value = chosen
    DoUpdate False
    Exit Sub
Fail:
    MsgBox Err.Description, vbExclamation, "Citrello"
End Sub

Private Function DocStyle(doc As Document) As String
    On Error Resume Next
    DocStyle = doc.Variables("CitrelloStyle").Value
    If Err.Number <> 0 Then
        Err.Clear
        DocStyle = doc.Variables("RefciteStyle").Value   ' chosen with an earlier version
        If Err.Number <> 0 Then DocStyle = ""
    End If
End Function

' ---------------- talking to Citrello ----------------
Private Sub StartListener()
    On Error Resume Next
    Application.Run "CitrelloStartListening"   ' click-to-see listener, in case Word skipped AutoExec
End Sub

Private Sub EnsureCitrello()
    Dim t As Single
    StartListener
    If IsRunning() Then Exit Sub
    If CITRELLO_CMD = "" Then Err.Raise vbObjectError + 2, , "Citrello is not running. Start Citrello and try again."
    Application.StatusBar = "Citrello: starting..."
    Shell CITRELLO_CMD, vbMinimizedNoFocus
    t = Timer
    Do While Timer - t < 20
        DoEvents
        If IsRunning() Then
            Application.StatusBar = ""
            Exit Sub
        End If
    Loop
    Err.Raise vbObjectError + 2, , "Citrello did not start. Start Citrello and try again."
End Sub

Private Function IsRunning() As Boolean
    Dim x As Object
    On Error GoTo Nope
    Set x = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    x.setTimeouts 1000, 1000, 2000, 2000
    x.Open "GET", CITRELLO_URL & "/api/ping", False
    x.send
    IsRunning = (x.Status = 200)
    Exit Function
Nope:
    IsRunning = False
End Function

Public Function CitrelloHttp(ByVal method As String, ByVal path As String, ByVal body As String) As String
    Dim x As Object
    Set x = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    x.setTimeouts 2000, 5000, 300000, 300000
    x.Open method, CITRELLO_URL & path, False
    x.setRequestHeader "Content-Type", "application/json; charset=utf-8"
    If body = "" Then x.send Else x.send body
    If x.Status <> 200 Then Err.Raise vbObjectError + 1, , "Citrello: " & x.responseText
    CitrelloHttp = x.responseText
End Function

Private Function TryHttp(ByVal method As String, ByVal path As String, ByVal body As String, ByRef msg As String) As String
    On Error GoTo Nope
    TryHttp = CitrelloHttp(method, path, body)
    Exit Function
Nope:
    msg = Replace(Err.Description, "Citrello: ", "")
    TryHttp = ""
End Function

Public Function CitrelloJson(ByVal s As String) As String
    Dim i As Long, c As String, code As Long, out As String
    For i = 1 To Len(s)
        c = Mid(s, i, 1)
        code = AscW(c) And &HFFFF&   ' AscW is negative above &H7FFF
        Select Case code
            Case 34: out = out & "\"""
            Case 92: out = out & "\\"
            Case Is < 32: out = out & " "
            Case Is > 126: out = out & "\u" & Right("0000" & LCase(Hex(code)), 4)
            Case Else: out = out & c
        End Select
    Next i
    CitrelloJson = """" & out & """"
End Function

Public Function CitrelloUrlEncode(ByVal s As String) As String
    Dim i As Long, code As Long, c As String, out As String
    For i = 1 To Len(s)
        c = Mid(s, i, 1)
        code = AscW(c) And &HFFFF&
        If (code >= 48 And code <= 57) Or (code >= 65 And code <= 90) Or (code >= 97 And code <= 122) Or c = "-" Or c = "." Or c = "_" Then
            out = out & c
        ElseIf code < &H80 Then
            out = out & "%%" & Right("0" & Hex(code), 2)
        ElseIf code < &H800 Then
            out = out & "%%" & Hex(&HC0 Or (code \ &H40)) & "%%" & Hex(&H80 Or (code And &H3F))
        Else
            out = out & "%%" & Hex(&HE0 Or (code \ &H1000)) & "%%" & Hex(&H80 Or ((code \ &H40) And &H3F)) & "%%" & Hex(&H80 Or (code And &H3F))
        End If
    Next i
    CitrelloUrlEncode = out
End Function
'''

# The citation dialog (a UserForm; its controls are created by the installer, see INSTALL_PS1).
FORM_CITE = r'''Option Explicit
Public Cancelled As Boolean
Public ResultJson As String

Private Sub UserForm_Initialize()
    Cancelled = True
    lstResults.ColumnCount = 2
    lstResults.ColumnWidths = "0 pt;640 pt"
    lstChosen.ColumnCount = 4
    lstChosen.ColumnWidths = "0 pt;0 pt;0 pt;640 pt"
    RunSearch ""
End Sub

Public Sub Prefill(ByVal rows As String)
    Dim lines() As String, i As Long, p() As String
    If Len(rows) = 0 Then Exit Sub
    lines = Split(rows, vbLf)
    For i = 0 To UBound(lines)
        p = Split(lines(i), vbTab)
        If UBound(p) >= 4 Then
            AddChosen p(0), p(1), p(2), p(4)
            If p(3) = "1" Then chkYearOnly.Value = True
        End If
    Next i
    cmdInsert.Caption = "Update"
    Me.Caption = "Citrello: edit citation"
End Sub

Private Sub RunSearch(ByVal q As String)
    Dim raw As String, rows() As String, i As Long, p As Long
    lstResults.Clear
    raw = CitrelloHttp("GET", "/api/library/search.tsv?n=5000&q=" & CitrelloUrlEncode(q), "")
    If Len(raw) > 0 Then
        rows = Split(raw, vbLf)
        For i = 0 To UBound(rows)
            p = InStr(rows(i), vbTab)
            If p > 0 Then
                lstResults.AddItem Left(rows(i), p - 1)
                lstResults.List(lstResults.ListCount - 1, 1) = Mid(rows(i), p + 1)
            End If
        Next i
    End If
    If lstResults.ListCount = 0 Then
        lblInfo.Caption = "Nothing found. Add it in Citrello, or ask Claude to add it."
    Else
        If Len(q) = 0 Then
            lblInfo.Caption = "All " & lstResults.ListCount & " references (newest first) - type to filter; double-click or Enter adds."
        Else
            lblInfo.Caption = lstResults.ListCount & " found - double-click or press Enter to add to the citation."
        End If
        lstResults.ListIndex = 0
    End If
End Sub

Private Sub AddChosen(ByVal key As String, ByVal display As String, ByVal locator As String, ByVal prefix As String)
    Dim i As Long, shown As String
    For i = 0 To lstChosen.ListCount - 1
        If lstChosen.List(i, 0) = key Then Exit Sub
    Next i
    shown = display
    If Len(prefix) > 0 Then shown = prefix & " " & shown
    If Len(locator) > 0 Then shown = shown & ", p. " & locator
    lstChosen.AddItem key
    lstChosen.List(lstChosen.ListCount - 1, 1) = locator
    lstChosen.List(lstChosen.ListCount - 1, 2) = prefix
    lstChosen.List(lstChosen.ListCount - 1, 3) = shown
End Sub

Private Sub AddSelected()
    Dim i As Long
    i = lstResults.ListIndex
    If i < 0 And lstResults.ListCount > 0 Then i = 0
    If i < 0 Then Exit Sub
    AddChosen CStr(lstResults.List(i, 0)), CStr(lstResults.List(i, 1)), Trim(txtPage.Text), Trim(txtPrefix.Text)
    txtPage.Text = ""
    txtPrefix.Text = ""
    txtSearch.SetFocus
    txtSearch.SelStart = 0
    txtSearch.SelLength = Len(txtSearch.Text)
End Sub

Private Sub txtSearch_Change()
    RunSearch txtSearch.Text
End Sub

Private Sub txtSearch_KeyDown(ByVal KeyCode As MSForms.ReturnInteger, ByVal Shift As Integer)
    If KeyCode = vbKeyReturn Then
        AddSelected
        KeyCode = 0
    ElseIf KeyCode = vbKeyDown And lstResults.ListCount > 0 Then
        lstResults.SetFocus
        KeyCode = 0
    End If
End Sub

Private Sub lstResults_DblClick(ByVal Cancel As MSForms.ReturnBoolean)
    AddSelected
End Sub

Private Sub lstResults_KeyDown(ByVal KeyCode As MSForms.ReturnInteger, ByVal Shift As Integer)
    If KeyCode = vbKeyReturn Then
        AddSelected
        KeyCode = 0
    End If
End Sub

Private Sub cmdAdd_Click()
    AddSelected
End Sub

Private Sub lstChosen_DblClick(ByVal Cancel As MSForms.ReturnBoolean)
    If lstChosen.ListIndex >= 0 Then lstChosen.RemoveItem lstChosen.ListIndex
End Sub

Private Sub cmdRemove_Click()
    If lstChosen.ListIndex >= 0 Then lstChosen.RemoveItem lstChosen.ListIndex
End Sub

Private Sub cmdInsert_Click()
    Dim i As Long, out As String, sup As String
    If lstChosen.ListCount = 0 Then AddSelected
    If lstChosen.ListCount = 0 Then Exit Sub
    If chkYearOnly.Value Then sup = "true" Else sup = "false"
    For i = 0 To lstChosen.ListCount - 1
        If i > 0 Then out = out & ","
        out = out & "{""key"":" & CitrelloJson(CStr(lstChosen.List(i, 0))) & ",""locator"":" & CitrelloJson(CStr(lstChosen.List(i, 1))) & _
              ",""prefix"":" & CitrelloJson(CStr(lstChosen.List(i, 2))) & ",""suppress"":" & sup & "}"
    Next i
    ResultJson = "[" & out & "]"
    Cancelled = False
    Me.Hide
End Sub

Private Sub cmdCancel_Click()
    Me.Hide
End Sub

Private Sub UserForm_QueryClose(Cancel As Integer, CloseMode As Integer)
    If CloseMode = vbFormControlMenu Then
        Cancel = True
        Me.Hide
    End If
End Sub
'''

FORM_STYLE = r'''Option Explicit
Public Cancelled As Boolean
Public Chosen As String

Private Sub UserForm_Initialize()
    Dim raw As String, lines() As String, i As Long, p As Long
    Cancelled = True
    lstStyles.ColumnCount = 2
    lstStyles.ColumnWidths = "0 pt;400 pt"
    raw = CitrelloHttp("GET", "/api/styles.txt", "")
    lines = Split(raw, vbLf)
    For i = 0 To UBound(lines)
        p = InStr(lines(i), "|")
        If p > 0 Then
            lstStyles.AddItem Left(lines(i), p - 1)
            lstStyles.List(lstStyles.ListCount - 1, 1) = Mid(lines(i), p + 1)
        End If
    Next i
End Sub

Public Sub SetCurrent(ByVal styleId As String)
    Dim i As Long
    For i = 0 To lstStyles.ListCount - 1
        If lstStyles.List(i, 0) = styleId Then
            lstStyles.ListIndex = i
            lblInfo.Caption = "Citation style for this document (now: " & lstStyles.List(i, 1) & ")"
            Exit Sub
        End If
    Next i
    If Len(styleId) > 0 Then
        txtOther.Text = styleId
        lblInfo.Caption = "Citation style for this document (now: " & styleId & ")"
    Else
        lblInfo.Caption = "Citation style for this document (now: Citrello's default)"
    End If
End Sub

Private Sub lstStyles_Click()
    txtOther.Text = ""
End Sub

Private Sub lstStyles_DblClick(ByVal Cancel As MSForms.ReturnBoolean)
    cmdOK_Click
End Sub

Private Sub cmdOK_Click()
    If Len(Trim(txtOther.Text)) > 0 Then
        Chosen = LCase(Trim(txtOther.Text))
    ElseIf lstStyles.ListIndex >= 0 Then
        Chosen = CStr(lstStyles.List(lstStyles.ListIndex, 0))
    Else
        Exit Sub
    End If
    Cancelled = False
    Me.Hide
End Sub

Private Sub cmdCancel_Click()
    Me.Hide
End Sub

Private Sub UserForm_QueryClose(Cancel As Integer, CloseMode As Integer)
    If CloseMode = vbFormControlMenu Then
        Cancel = True
        Me.Hide
    End If
End Sub
'''

# Listens to Word: a click in a citation shows its reference, a double-click opens it for editing.
EVENTS_CLASS = r'''Option Explicit
Public WithEvents App As Word.Application

Private Sub App_WindowSelectionChange(ByVal Sel As Selection)
    On Error Resume Next
    If Sel.Information(45) Then      ' 45 = wdInFieldResult: cheap check, so typing elsewhere is not slowed down
        CitrelloShowAt Sel
    Else
        CitrelloClearShown
    End If
End Sub

Private Sub App_WindowBeforeDoubleClick(ByVal Sel As Selection, Cancel As Boolean)
    On Error Resume Next
    If Sel.Information(45) Then
        If CitrelloEditAt(Sel) Then Cancel = True
    End If
End Sub
'''

EVENTS_START = r'''Option Explicit
' (the variable must not be called like the class: VBA ignores case, so that would not compile)
Private evHandler As CitrelloEvents

' Word runs AutoExec when it loads this template at start-up
Public Sub AutoExec()
    CitrelloStartListening
End Sub

Public Sub CitrelloStartListening()
    On Error Resume Next
    If evHandler Is Nothing Then
        Set evHandler = New CitrelloEvents
        Set evHandler.App = Application
    End If
End Sub

Public Function CitrelloListening() As Boolean
    CitrelloListening = Not (evHandler Is Nothing)
End Function
'''

RIBBON = '''<customUI xmlns="http://schemas.microsoft.com/office/2009/07/customui">
  <ribbon>
    <tabs>
      <tab id="citrelloTab" label="Citrello">
        <group id="citrelloCitations" label="Citations">
          <button id="citrelloInsert" label="Insert / Edit citation" size="large" imageMso="CitationInsert"
                  screentip="Insert or edit a citation"
                  supertip="Search your Citrello library and insert a citation at the cursor. With the cursor in a citation, edit it."
                  onAction="CitrelloInsert"/>
          <button id="citrelloUpdate" label="Update citations" size="large" imageMso="Refresh"
                  screentip="Update citations"
                  supertip="Renumber and reformat all citations and the bibliography, turn [@Key] markers into citations. One Undo step."
                  onAction="CitrelloUpdate"/>
          <button id="citrelloStyle" label="Citation style" size="large" imageMso="Bibliography"
                  screentip="Citation style" supertip="Choose the citation style for this document."
                  onAction="CitrelloStyle"/>
        </group>
        <group id="citrelloLibrary" label="Library">
          <button id="citrelloOpen" label="Open Citrello" size="large" imageMso="FileOpen"
                  screentip="Open Citrello" supertip="Open your Citrello library." onAction="CitrelloOpen"/>
          <button id="citrelloDiagnose" label="Diagnostics" size="normal" imageMso="Help"
                  screentip="Diagnostics" supertip="Write what Word sees about the citations into a new document (to send when something goes wrong)."
                  onAction="CitrelloDiagnose"/>
        </group>
      </tab>
    </tabs>
  </ribbon>
</customUI>
'''

INSTALL_PS1 = r'''param([string]$CodePath, [string]$CiteFormPath, [string]$StyleFormPath, [string]$EventsPath, [string]$StartPath, [string]$OutPath)
$ErrorActionPreference = 'Stop'
$key = $null
foreach ($v in '16.0', '15.0', '14.0') {
  if (Test-Path "HKCU:\Software\Microsoft\Office\$v\Word") { $key = "HKCU:\Software\Microsoft\Office\$v\Word\Security"; break }
}
if (-not $key) { throw 'Microsoft Word was not found for this Windows user.' }
if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
$old = (Get-ItemProperty -Path $key -Name AccessVBOM -ErrorAction SilentlyContinue).AccessVBOM
Set-ItemProperty -Path $key -Name AccessVBOM -Value 1 -Type DWord

function Set-Code($component, [string]$path) {
  $cm = $component.CodeModule
  if ($cm.CountOfLines -gt 0) { $cm.DeleteLines(1, $cm.CountOfLines) }   # e.g. an automatic "Option Explicit"
  $cm.AddFromString([IO.File]::ReadAllText($path))
}
function Add-Ctl($form, [string]$progId, [string]$name, $l, $t, $w, $h, [string]$caption) {
  $c = $form.Designer.Controls.Add($progId)
  $c.Name = $name
  $c.Left = $l; $c.Top = $t; $c.Width = $w; $c.Height = $h
  if ($caption) { $c.Caption = $caption }
  return $c
}
function New-Form($project, [string]$name, [string]$caption, $w, $h) {
  $f = $project.VBComponents.Add(3)
  $f.Name = $name
  $f.Properties.Item('Caption').Value = $caption
  $f.Properties.Item('Width').Value = $w
  $f.Properties.Item('Height').Value = $h
  try { $f.Designer.Font.Name = 'Segoe UI'; $f.Designer.Font.Size = 9 } catch { }
  return $f
}

$word = $null
try {
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  $doc = $word.Documents.Add()
  $proj = $doc.VBProject

  $mod = $proj.VBComponents.Add(1)
  $mod.Name = 'Citrello'
  Set-Code $mod $CodePath

  $cls = $proj.VBComponents.Add(2)
  $cls.Name = 'CitrelloEvents'
  Set-Code $cls $EventsPath
  $start = $proj.VBComponents.Add(1)
  $start.Name = 'CitrelloStart'
  Set-Code $start $StartPath

  $f = New-Form $proj 'CitrelloCite' 'Citrello: insert citation' 500 452
  [void](Add-Ctl $f 'Forms.Label.1' 'lblSearch' 12 8 470 14 'Search your Citrello library (author, title, year or key)')
  [void](Add-Ctl $f 'Forms.TextBox.1' 'txtSearch' 12 24 470 22 '')
  [void](Add-Ctl $f 'Forms.ListBox.1' 'lstResults' 12 50 470 170 '')
  [void](Add-Ctl $f 'Forms.Label.1' 'lblInfo' 12 224 470 14 '')
  [void](Add-Ctl $f 'Forms.Label.1' 'lblPage' 12 246 34 14 'Page')
  [void](Add-Ctl $f 'Forms.TextBox.1' 'txtPage' 44 243 60 20 '')
  [void](Add-Ctl $f 'Forms.Label.1' 'lblPrefix' 118 246 40 14 'Prefix')
  [void](Add-Ctl $f 'Forms.TextBox.1' 'txtPrefix' 156 243 90 20 '')
  [void](Add-Ctl $f 'Forms.Label.1' 'lblPrefixHint' 252 246 120 14 '(e.g. see, e.g.)')
  [void](Add-Ctl $f 'Forms.CommandButton.1' 'cmdAdd' 382 241 100 24 'Add to citation')
  [void](Add-Ctl $f 'Forms.Label.1' 'lblChosen' 12 274 470 14 'Citation (double-click an entry to remove it)')
  [void](Add-Ctl $f 'Forms.ListBox.1' 'lstChosen' 12 290 470 76 '')
  [void](Add-Ctl $f 'Forms.CheckBox.1' 'chkYearOnly' 12 372 360 18 'Year only - the author name is already in my sentence')
  [void](Add-Ctl $f 'Forms.CommandButton.1' 'cmdRemove' 382 370 100 22 'Remove')
  [void](Add-Ctl $f 'Forms.CommandButton.1' 'cmdInsert' 276 400 100 26 'Insert')
  [void](Add-Ctl $f 'Forms.CommandButton.1' 'cmdCancel' 382 400 100 26 'Cancel')
  Set-Code $f $CiteFormPath

  $s = New-Form $proj 'CitrelloStyleForm' 'Citrello: citation style' 392 362
  [void](Add-Ctl $s 'Forms.Label.1' 'lblInfo' 12 8 362 28 'Citation style for this document')
  [void](Add-Ctl $s 'Forms.ListBox.1' 'lstStyles' 12 38 362 200 '')
  [void](Add-Ctl $s 'Forms.Label.1' 'lblOther' 12 246 362 14 'Or any other journal style id from zotero.org/styles (e.g. optica):')
  [void](Add-Ctl $s 'Forms.TextBox.1' 'txtOther' 12 262 362 20 '')
  [void](Add-Ctl $s 'Forms.CommandButton.1' 'cmdOK' 178 296 96 26 'Apply')
  [void](Add-Ctl $s 'Forms.CommandButton.1' 'cmdCancel' 278 296 96 26 'Cancel')
  Set-Code $s $StyleFormPath

  $doc.SaveAs2($OutPath, 15)
  $doc.Close(0)
  Write-Output ('STARTUP=' + $word.StartupPath)
} finally {
  if ($word) { $word.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word) }
  if ($null -eq $old) { Remove-ItemProperty -Path $key -Name AccessVBOM -ErrorAction SilentlyContinue }
  else { Set-ItemProperty -Path $key -Name AccessVBOM -Value $old -Type DWord }
}
'''


def citrello_command():
    """Command line the Word macro uses to start Citrello when it is not running."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --no-browser'
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    if os.path.exists(pyw):
        py = pyw
    app = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    return f'"{py}" "{app}" --no-browser'


def _check_vba(code):
    """Catch damaged lines (e.g. a lost string literal) before they reach Word as a compile error."""
    seen_proc = False
    for n, line in enumerate(code.splitlines(), 1):
        if re.match(r"^(Public |Private )?(Sub|Function) ", line):
            seen_proc = True
        elif seen_proc and re.match(r"^(Public|Private|Dim|Const)\s+(?!Sub\b|Function\b)\w+", line):
            raise ValueError(f"Module-level declaration after a procedure (line {n}): {line.strip()}")
        stmt = re.sub(r'"(?:[^"]|"")*"', '""', line).split("'")[0].rstrip()
        stmt = re.sub(r"&H[0-9A-F]+&?", "0", stmt)
        if re.search(r"(=|&)\s*(&|$)|\(\s*&|&\s*\)", stmt) and not stmt.endswith("_"):
            raise ValueError(f"Damaged VBA line {n}: {line.strip()}")
    return code


def vba_code():
    cmd = citrello_command().replace('"', '""')  # VBA string literal
    return _check_vba(VBA % {"port": PORT, "cmd": cmd})


def bas_file():
    """The same macro as a .bas file, for installing by hand (VBA editor > File > Import)."""
    return 'Attribute VB_Name = "Citrello"\r\n' + vba_code().replace("\n", "\r\n")


def add_ribbon(dotm_path):
    """Add the Citrello ribbon tab (customUI14) to a macro-enabled template."""
    tmp = dotm_path + ".tmp"
    with zipfile.ZipFile(dotm_path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "_rels/.rels":
                rel = ('<Relationship Id="citrelloRibbon" '
                       'Type="http://schemas.microsoft.com/office/2007/relationships/ui/extensibility" '
                       'Target="customUI/customUI14.xml"/>')
                text = data.decode("utf-8")
                if "citrelloRibbon" not in text:
                    text = text.replace("</Relationships>", rel + "</Relationships>")
                data = text.encode("utf-8")
            if item.filename.startswith("customUI/"):
                continue
            dst.writestr(item, data)
        dst.writestr("customUI/customUI14.xml", RIBBON)
    os.replace(tmp, dotm_path)


def _default_startup():
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Microsoft", "Word", "STARTUP")


OLD_TEMPLATE = "Refcite.dotm"  # an earlier version's template; removed on install


def installed_paths():
    return [p for p in glob.glob(os.path.join(_default_startup(), TEMPLATE_NAME))]


def status():
    return {"windows": sys.platform.startswith("win"), "installed": bool(installed_paths()),
            "paths": installed_paths()}


def install():
    if not sys.platform.startswith("win"):
        raise RuntimeError("The Word button can be installed on Windows only.")
    work = tempfile.mkdtemp(prefix="citrello_word_")
    try:
        code, cite, sty, evc, evs, ps1, out = (os.path.join(work, n) for n in
                                               ("Citrello.vba", "CitrelloCite.vba", "CitrelloStyle.vba", "CitrelloEvents.vba",
                                                "CitrelloStart.vba", "install.ps1", TEMPLATE_NAME))
        open(code, "w", encoding="utf-8").write(vba_code())
        open(cite, "w", encoding="utf-8").write(_check_vba(FORM_CITE))
        open(sty, "w", encoding="utf-8").write(_check_vba(FORM_STYLE))
        open(evc, "w", encoding="utf-8").write(_check_vba(EVENTS_CLASS))
        open(evs, "w", encoding="utf-8").write(_check_vba(EVENTS_START))
        open(ps1, "w", encoding="utf-8-sig").write(INSTALL_PS1)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-File", ps1, "-CodePath", code, "-CiteFormPath", cite, "-StyleFormPath", sty,
                            "-EventsPath", evc, "-StartPath", evs, "-OutPath", out],
                           capture_output=True, text=True, timeout=180,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not os.path.exists(out):
            msg = (r.stderr or r.stdout or "").strip().splitlines()
            raise RuntimeError("Word could not create the button: " + (" ".join(msg[-3:]) if msg else "unknown error")
                               + ". You can install it by hand instead (see below).")
        startup = next((l.split("=", 1)[1].strip() for l in r.stdout.splitlines() if l.startswith("STARTUP=")), "")
        startup = startup or _default_startup()
        add_ribbon(out)
        os.makedirs(startup, exist_ok=True)
        dest = os.path.join(startup, TEMPLATE_NAME)
        old = os.path.join(startup, OLD_TEMPLATE)
        try:
            if os.path.exists(old):
                os.remove(old)  # otherwise Word would show two tabs
            shutil.copyfile(out, dest)
        except PermissionError:
            raise RuntimeError("Close Word first: it is using the old Citrello button. Then click Install again.")
        return {"installed": True, "path": dest}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def remove():
    removed = []
    for p in installed_paths():
        try:
            os.remove(p)
            removed.append(p)
        except PermissionError:
            raise RuntimeError("Close Word first, then click Remove again.")
    return {"removed": removed}


# --------------------------------------------------------------------------
# Updating the document Word just closed
# --------------------------------------------------------------------------

def _backup(path, keep=30):
    folder = os.path.join(library.HOME, "backups")
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(folder, f"{os.path.splitext(os.path.basename(path))[0]} {stamp}.docx")
    shutil.copyfile(path, dest)
    old = sorted(glob.glob(os.path.join(folder, "*.docx")), key=os.path.getmtime)
    for p in old[:-keep]:
        os.remove(p)
    return dest


def update_file(path, style=None, default_style=None):
    """Update a .docx in place (after a backup). Returns a one-line message for Word's status bar."""
    path = os.path.abspath(path)
    if not path.lower().endswith(".docx") or not os.path.isfile(path):
        raise ValueError(f"Not a .docx file: {path}")
    backup = _backup(path)
    fd, tmp = tempfile.mkstemp(suffix=".docx", dir=os.path.dirname(path))
    os.close(fd)
    try:
        res = citeformat.update_document(path, tmp, style or default_style)
        os.replace(tmp, path)
    except PermissionError:
        raise ValueError("Word still has the document open. Close it and try again.")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    msg = (f"{res['citations']} citation(s) and {res['entries']} bibliography entries updated "
           f"({res['style']['title']}).")
    if res["report"]:
        msg += " Note: " + " | ".join(res["report"])
    return msg, backup
