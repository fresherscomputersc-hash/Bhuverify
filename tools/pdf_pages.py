"""Render ALL PDF pages and OCR each (current pipeline only sees page 1)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pypdfium2 as pdfium

pdf_path = Path("sample/dfgh.pdf")
pdf = pdfium.PdfDocument(str(pdf_path))
print("pages:", len(pdf))
for i, page in enumerate(pdf):
    bitmap = page.render(scale=300 / 72)
    out = Path(f"data/processed/dfgh_p{i + 1}.png")
    bitmap.to_pil().save(str(out), "PNG")
    print("wrote", out, bitmap.width, "x", bitmap.height)
pdf.close()
