import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))  # shared helpers live in src/
import pypdfium2 as pdfium
pdf = pdfium.PdfDocument("report_export.pdf")
for i in range(len(pdf)):
    pdf[i].render(scale=1.6).to_pil().save(f"page{i+1}.png")
print(len(pdf), "pages")
