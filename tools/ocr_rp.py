import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pypdfium2 as pdfium
from app.services.ocr_service import run_ocr


def _a(s):
    return (s or "").encode("ascii", "replace").decode("ascii")


pdf = pdfium.PdfDocument("sample/rpserdff.pdf")
print("pages:", len(pdf))
for i, page in enumerate(pdf):
    out = Path(f"data/processed/rp_p{i + 1}.png")
    page.render(scale=300 / 72).to_pil().save(str(out), "PNG")
    r = run_ocr(str(out), language="eng+hin+ori", psm=4)
    print("=" * 80)
    print("PAGE", i + 1, "words:", r.word_count, "conf:", round(r.mean_confidence, 1))
    for j, ln in enumerate(r.text.splitlines()):
        if ln.strip():
            print("%3d %s" % (j, _a(ln)[:140]))
pdf.close()
