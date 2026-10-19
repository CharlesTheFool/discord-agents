---
name: xlsx
description: Build, read, or clean up spreadsheets (.xlsx, .csv) with openpyxl and pandas in the workshop - tables, formulas, formatting, charts, quick analysis.
---

# Spreadsheets

openpyxl and pandas are installed in the workshop. pandas is the fast way to
read, reshape, and analyze; openpyxl is how you make a workbook people will
open and use (formulas, formatting, charts).

## Reading and analyzing

Someone's spreadsheet lands in /workspace/inbox/ when you fetch it.

```python
import pandas as pd
sheets = pd.read_excel("/workspace/inbox/budget.xlsx", sheet_name=None)  # every sheet
for name, df in sheets.items():
    print(name, df.shape)
    print(df.head(10).to_string())
df = pd.read_csv("/workspace/inbox/export.csv")
```

Look before you compute: check the header row, the dtypes (`df.dtypes`), blank
rows, merged-cell artifacts, and totals rows mixed in with data. Say what you
assumed when the data was ambiguous.

## Making a workbook

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import CellIsRule

wb = Workbook()
ws = wb.active
ws.title = "Budget"
ws.append(["Category", "Planned", "Actual", "Difference"])
rows = [("Rent", 1200, 1200), ("Food", 400, 455), ("Transit", 90, 72)]
for i, (cat, planned, actual) in enumerate(rows, start=2):
    ws.append([cat, planned, actual, f"=C{i}-B{i}"])
last = len(rows) + 1
ws.append(["Total", f"=SUM(B2:B{last})", f"=SUM(C2:C{last})", f"=SUM(D2:D{last})"])

for cell in ws[1]:
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="1F3A5F")
    cell.alignment = Alignment(horizontal="center")
for col in "BCD":
    for cell in ws[col][1:]:
        cell.number_format = "#,##0.00"
ws.column_dimensions["A"].width = 16
ws.freeze_panes = "A2"
ws.auto_filter.ref = f"A1:D{last}"
ws.conditional_formatting.add(f"D2:D{last}", CellIsRule(
    operator="greaterThan", formula=["0"], font=Font(color="C00000")))

chart = BarChart()
chart.title = "Planned vs actual"
chart.add_data(Reference(ws, min_col=2, max_col=3, min_row=1, max_row=last), titles_from_data=True)
chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=last))
ws.add_chart(chart, "F2")
wb.save("/workspace/budget.xlsx")
```

- Prefer formulas over values you computed in Python when people will change
  the inputs: the sheet stays live.
- openpyxl writes formulas but never calculates them. Excel and Google Sheets
  calculate on open; reading the file back with openpyxl (even
  `data_only=True`) shows the formula or None, not the result. When you need
  the numbers yourself, compute them in pandas.
- From a DataFrame: `df.to_excel(path, index=False)`, then reopen with
  `openpyxl.load_workbook(path)` to format it.
- Dates: write real `datetime` values and set `number_format = "yyyy-mm-dd"`,
  not strings.

## Editing one

`load_workbook(path)` keeps values, formulas, styles, and merged cells, but it
drops things openpyxl doesn't model (pivot tables, slicers, some charts and
images). For a workbook with those, write your changes to a copy and say what
might not survive. Save under a new name so the original stays intact.

## Delivering

Save into /workspace, then attach it with send_message (`attach_outputs` takes
its /workspace path). A short summary of what's in it belongs in the message;
a chart people should see right away can ride along as a matplotlib PNG. If
the server should keep the file, `repository` save_output copies it onto the
shelf.
