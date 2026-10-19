---
name: pdf
description: Work with PDFs in the workshop - extract text, merge, split, rotate, fill forms, stamp pages (pypdf), and make new PDFs from text, tables, and charts (reportlab).
---

# PDFs

pypdf (reading and reshaping) and reportlab (making new ones) are installed in
the workshop, along with matplotlib for charts.

A small PDF someone shares you can usually just read. When you fetch one with
get_attachment where the workshop is on, a copy also lands in /workspace/inbox/,
which is the one to work on.

## Reading and extracting

```python
from pypdf import PdfReader
reader = PdfReader("/workspace/inbox/report.pdf")
print(len(reader.pages), reader.metadata)
for i, page in enumerate(reader.pages, 1):
    print(f"--- page {i}\n{page.extract_text()}")
```

Extraction follows the PDF's internal layout, so columns and tables can come
out interleaved; check before quoting numbers. A scanned PDF has no text layer
(extract_text returns little or nothing) and there's no OCR in the workshop:
say so rather than guess. Encrypted: `reader.decrypt(password)` first.

## Reshaping

```python
from pypdf import PdfReader, PdfWriter

writer = PdfWriter()                                   # merge
for path in ["/workspace/inbox/a.pdf", "/workspace/inbox/b.pdf"]:
    writer.append(path)
writer.write("/workspace/merged.pdf")

reader = PdfReader("/workspace/inbox/a.pdf")           # pages 2-4, the last one rotated
writer = PdfWriter()
for page in reader.pages[1:4]:
    writer.add_page(page)
writer.pages[-1].rotate(90)
writer.write("/workspace/excerpt.pdf")

stamp = PdfReader("/workspace/stamp.pdf").pages[0]     # stamp or watermark every page
writer = PdfWriter(clone_from="/workspace/inbox/a.pdf")
for page in writer.pages:
    page.merge_page(stamp)
writer.write("/workspace/stamped.pdf")
```

Filling a form:

```python
reader = PdfReader("/workspace/inbox/form.pdf")
print(reader.get_fields().keys())                      # the field names to fill
writer = PdfWriter(clone_from=reader)
writer.update_page_form_field_values(writer.pages[0], {"Name": "Ada Lovelace"},
                                     auto_regenerate=False)
writer.set_need_appearances_writer(True)
writer.write("/workspace/form-filled.pdf")
```

## Making one

reportlab's platypus lays out flowing documents: paragraphs, tables, images,
page breaks. Its built-in fonts only cover Western European characters, so
register DejaVu (shipped with matplotlib) to handle accents, Greek, Cyrillic,
and symbols.

```python
import os
from xml.sax.saxutils import escape
from matplotlib import get_data_path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

fonts = os.path.join(get_data_path(), "fonts", "ttf")
pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(fonts, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(fonts, "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold")
styles = getSampleStyleSheet()
for style in styles.byName.values():
    style.fontName = "DejaVu-Bold" if style.name.startswith(("Heading", "Title")) else "DejaVu"

story = [
    Paragraph("Trip budget", styles["Title"]),
    Paragraph(escape("Costs for the Montréal weekend, split 3 ways & rounded."), styles["Normal"]),
    Spacer(1, 0.2 * inch),
]
table = Table([["Item", "Total", "Each"], ["Hotel", "540.00", "180.00"], ["Food", "210.00", "70.00"]],
              colWidths=[2.5 * inch, 1.2 * inch, 1.2 * inch])
table.setStyle(TableStyle([
    ("FONTNAME", (0, 0), (-1, -1), "DejaVu"),
    ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3A5F")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
]))
story += [table, Spacer(1, 0.3 * inch), Image("/workspace/chart.png", width=6 * inch, height=3.5 * inch)]
SimpleDocTemplate("/workspace/budget.pdf", pagesize=letter).build(story)
```

- Paragraph text is a small markup language (`<b>`, `<i>`, `<br/>`): run
  anything you didn't write yourself through `escape()`, or a stray `&` or `<`
  breaks the build.
- `Image` needs a width and height that keep the picture's aspect ratio; for a
  matplotlib figure, use its figsize ratio.
- `PageBreak()` starts a new page; `pagesize=A4` (from
  `reportlab.lib.pagesizes`) outside North America.
- A chart-only PDF is simpler with matplotlib alone:
  `fig.savefig("/workspace/chart.pdf")`.

There's no Word or LibreOffice to convert documents, and no renderer to look
at the result: read your PDF back with PdfReader to check its pages and text.

## Delivering

Save into /workspace, then attach it with send_message (`attach_outputs` takes
its /workspace path). If the server should keep it, `repository` save_output
copies it onto the shelf.
