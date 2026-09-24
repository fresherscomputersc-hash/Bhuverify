"""Check whether key ground-truth tokens survive the combined OCR pass."""
import json
import sys


def _a(s):
    return (s or '').encode('ascii', 'replace').decode('ascii')


r = json.load(open('data/reports/extraction_analysis.json', encoding='utf-8'))
checks = {
    'dfgh.pdf': ['\u0b17\u0b4b\u0b20\u0b2c\u0b23', '\u0b16\u0b47\u0b71\u0b3e\u0b1f',
                 '\u0b2a\u0b4d\u0b30\u0b1c\u0b3e', '\u0b30\u0b15\u0b2c\u0b3e',
                 '\u0b15\u0b3f\u0b38\u0b2e', '\u0b18\u0b30\u0b2c\u0b3e\u0b30\u0b3f',
                 '248/725', '149', '\u0b28\u0b5f\u0b3e\u0b17\u0b5c'],
    'pshg2-8dzkA9z6dlCPjQbDuc4i0VqFzlh8i21I8GoWKLxIRvbhyXuhyzsFYRXtWtinW0_JZ3Bjkj4Ab62iK83Mt7MEeGrC83xD5ulw_TTP7J--qI9o6eNXuXCtPab1cqGt2G6ShqekAq8Rew0A2HAvy-2EB6bFEckepBmG4qqq25eAzZRa1d-Wz6_H4QmE5M.jpg':
        ['\u0932\u0916\u0928\u090a', '\u0917\u094b\u092a\u0930\u093e\u092e\u090a',
         '00183', '986', '1.7560',
         '\u0918\u0928\u0936\u094d\u092f\u093e\u092e', '\u0917\u093e\u091f\u093e',
         '\u0916\u093e\u0924\u093e', '\u091c\u0928\u092a\u0926',
         '\u0936\u094d\u0930\u0947\u0923\u0940'],
    'ChatGPT Image Sep 23, 2026, 02_33_50 PM.png':
        ['\u0915\u091f\u0915', '123', '456',
         '\u0930\u093e\u092e', '\u0915\u0943\u0937\u093f',
         '\u0930\u0915\u092c\u093e', '\u0916\u0947\u0938\u0930\u093e',
         '\u0917\u094d\u0930\u093e\u092e', '15/07/2024'],
}
by_file = {d['file']: d for d in r if 'ocr_text' in d}
for fname, tokens in checks.items():
    doc = by_file.get(fname)
    if not doc:
        print('MISSING', _a(fname))
        continue
    print('=' * 90)
    print('##', _a(fname))
    text = doc['ocr_text']
    for tok in tokens:
        print('  %-12s %s' % (_a(tok), 'FOUND' if tok in text else 'miss'))
