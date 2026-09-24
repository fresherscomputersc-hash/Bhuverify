"""Time candidate multi-pass OCR strategies on real samples."""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ocr_service import run_ocr

DEV = re.compile(r'[\u0900-\u097F]')
ODIA = re.compile(r'[\u0B00-\u0B7F]')


def run(f, lang):
    t = time.perf_counter()
    r = run_ocr(f, language=lang, psm=4)
    dt = time.perf_counter() - t
    dev = len(DEV.findall(r.text))
    odia = len(ODIA.findall(r.text))
    print('  %-14s %5.1fs words=%-4d conf=%5.1f dev=%-4d odia=%-4d' % (
        lang, dt, r.word_count, r.mean_confidence, dev, odia), flush=True)
    return r


FILES = [
    'data/processed/dfgh_page1.png',
    'sample/pshg2-8dzkA9z6dlCPjQbDuc4i0VqFzlh8i21I8GoWKLxIRvbhyXuhyzsFYRXtWtinW0_JZ3Bjkj4Ab62iK83Mt7MEeGrC83xD5ulw_TTP7J--qI9o6eNXuXCtPab1cqGt2G6ShqekAq8Rew0A2HAvy-2EB6bFEckepBmG4qqq25eAzZRa1d-Wz6_H4QmE5M.jpg',
]
for f in FILES:
    print('=' * 80)
    print(f, flush=True)
    for lang in ('hin+ori', 'eng+hin', 'eng+ori'):
        try:
            run(f, lang)
        except Exception as e:  # noqa: BLE001
            print('  %-14s ERROR %s' % (lang, e), flush=True)
