---
name: docx
description: Make, read, or edit Word documents (.docx) with python-docx in the workshop - reports, letters, handouts, filled-in templates.
---

# Word documents

python-docx is installed in the workshop. Write a short script in /workspace,
run it with bash, and keep the script: the next revision is an edit away.

## Making one

```python
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()
doc.styles["Normal"].font.name = "Calibri"
doc.styles["Normal"].font.size = Pt(11)

doc.add_heading("Quarterly notes", level=0)          # 0 = title, 1-9 = headings
p = doc.add_paragraph("Sales were ")
p.add_run("up 12%").bold = True
p.add_run(" on the quarter.")
doc.add_paragraph("First point", style="List Bullet")
doc.add_paragraph("Second point", style="List Number")

table = doc.add_table(rows=1, cols=3, style="Table Grid")
for cell, text in zip(table.rows[0].cells, ["Item", "Qty", "Price"]):
    cell.text = text
    cell.paragraphs[0].runs[0].bold = True
for row in [("Pens", "4", "3.00"), ("Paper", "2", "9.50")]:
    for cell, text in zip(table.add_row().cells, row):
        cell.text = text

doc.add_picture("/workspace/chart.png", width=Inches(6))
doc.add_page_break()
doc.sections[0].footer.paragraphs[0].text = "Draft"
doc.save("/workspace/notes.docx")
```

- Built-in style names that always exist: "Title", "Heading 1".."Heading 9",
  "List Bullet", "List Number", "Quote", "Table Grid". Others may not be in the
  default template; a missing style raises KeyError.
- Margins: `section.left_margin = Inches(1)`; landscape: swap
  `section.page_width` and `section.page_height` and set
  `section.orientation = WD_ORIENT.LANDSCAPE` (from `docx.enum.section`).
- Charts and diagrams: draw them with matplotlib, save a PNG at dpi=200, then
  `add_picture`.

## Reading and editing one

Someone's .docx lands in /workspace/inbox/ when you fetch it.

```python
doc = Document("/workspace/inbox/report.docx")
for p in doc.paragraphs:
    print(p.style.name, "|", p.text)
for t in doc.tables:
    for row in t.rows:
        print([c.text for c in row.cells])
```

Word splits text into runs wherever formatting changes, so a phrase can span
several runs. To replace text and keep formatting, edit inside the run that
holds it; setting `p.text` rewrites the paragraph as a single plain run. Save
edits to a new filename so the original stays intact.

## Limits

No Word or LibreOffice in the workshop: no tracked changes, no PDF
conversion, no rendered preview, and fields like a table of contents don't
update until someone opens the file in Word. For a PDF, see the pdf skill.
Check your work by reading the file back with the snippet above.

## Delivering

Save into /workspace, then attach it with send_message (`attach_outputs` takes
its /workspace path). If the server should keep it, `repository` save_output
copies it onto the shelf.
