import json
r = json.load(open('data/reports/extraction_analysis.json', encoding='utf-8'))
def _a(s):
    return (s or '').encode('ascii', 'replace').decode('ascii')


for doc in r:
    if 'error' in doc:
        print('##', _a(doc['file']), 'ERROR', _a(doc['error']))
        continue
    print('=' * 100)
    print('##', _a(doc['file']), '|', _a(doc['doc_type']),
          '| rec_conf:', doc['record_confidence'], '| area_ha:', doc['area_hectare'])
    print('missing:', [ _a(m) for m in doc['missing_required'] ])
    print('low:', [ _a(m) for m in doc['low_confidence'] ])
    for k in ('owner_name', 'khasra_no', 'khata_no', 'village', 'tehsil',
              'district', 'area', 'land_classification', 'mutation_date',
              'registration_no', 'survey_no', 'plot_no', 'guardian_name'):
        f = doc['fields'].get(k, {})
        v = _a((f.get('value', '') or '')[:55])
        n = _a((f.get('normalized', '') or '')[:35])
        print('  %-20s val=%-55s norm=%-35s conf=%s' % (k, v, n, f.get('conf')))
