---
name: pptx
description: Build or edit PowerPoint decks (.pptx) with python-pptx in the workshop - slides with text, images, tables, and native charts.
---

# PowerPoint decks

python-pptx is installed in the workshop. Write the deck as a script in
/workspace and run it with bash; revisions are then edits to the script.

## Before the code

Decide what each slide says in one line first. A good deck has one idea per
slide, a headline that states the point ("Churn halved after the redesign",
not "Churn"), few words, and one consistent pair of fonts and colors. Six
bullets of full sentences is a document; make a docx instead.

## Making one

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)   # 16:9

TITLE, TITLE_AND_CONTENT, TITLE_ONLY, BLANK = (prs.slide_layouts[i] for i in (0, 1, 5, 6))

s = prs.slides.add_slide(TITLE)
s.shapes.title.text = "Q3 review"
s.placeholders[1].text = "Support team"

s = prs.slides.add_slide(TITLE_AND_CONTENT)
s.shapes.title.text = "Response times fell all quarter"
body = s.placeholders[1].text_frame
body.text = "Median first reply: 4h to 40m"
sub = body.add_paragraph()
sub.text = "Weekend coverage did most of it"
sub.level = 1                                  # indent one level

s = prs.slides.add_slide(TITLE_ONLY)
s.shapes.title.text = "Tickets by month"
data = CategoryChartData()
data.categories = ["Jul", "Aug", "Sep"]
data.add_series("Tickets", (310, 280, 240))
s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1.6),
                   Inches(11.3), Inches(5.4), data)

s = prs.slides.add_slide(BLANK)
box = s.shapes.add_textbox(Inches(1), Inches(3), Inches(11.3), Inches(1.5))
run = box.text_frame.paragraphs[0].add_run()
run.text = "Questions?"
run.font.size, run.font.bold = Pt(48), True
run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)

prs.save("/workspace/q3-review.pptx")
```

- The default template's layouts were drawn for 4:3, so on a 16:9 deck their
  placeholders sit in the left 10 inches. Widen them
  (`ph.width = prs.slide_width - 2 * ph.left` for each `s.placeholders`), or
  place your own shapes on TITLE_ONLY and BLANK slides.
- Images: `s.shapes.add_picture(path, left, top, width=...)` (give width or
  height, not both, to keep the aspect ratio). matplotlib PNGs at dpi=200 look
  sharp.
- Tables: `s.shapes.add_table(rows, cols, left, top, width, height).table`,
  then `table.cell(r, c).text = ...`.
- Speaker notes: `s.notes_slide.notes_text_frame.text = "..."`.
- Native charts (above) stay editable in PowerPoint; prefer them over images
  of charts when the numbers matter.

## Editing one

Someone's deck lands in /workspace/inbox/ when you fetch it.

```python
prs = Presentation("/workspace/inbox/deck.pptx")
for i, slide in enumerate(prs.slides, 1):
    for shape in slide.shapes:
        if shape.has_text_frame:
            print(i, shape.shape_type, shape.name, "|", shape.text_frame.text)
```

Edit text through runs to keep the deck's formatting (`run.text = ...`);
setting `text_frame.text` resets it. Building on their file keeps their theme,
fonts, and layouts.

## Limits

There's no renderer in the workshop, so you can't see the slides. Keep text
short enough that it can't overflow (about 6 lines per body at 20pt), read
the file back with the loop above, and tell people it's worth a glance in
PowerPoint or Google Slides.

## Delivering

Save into /workspace, then attach it with send_message (`attach_outputs` takes
its /workspace path). If the server should keep it, `repository` save_output
copies it onto the shelf.
