import json
with open('dispatcher_cases_v2/data/dispatcher_story.json', encoding='utf-8') as f:
    data = json.load(f)
for s in data['scenes']:
    if s['id'] in ('bienvenida', 'intro', 'supervisor', 'test_authflow'):
        print('=== Scene:', s['id'], '===')
        for i, b in enumerate(s.get('beats', [])):
            print('  beat %d: kind=%-10s action=%-8s speaker=%-8s text=%.60s' % (
                i, b.get('kind','line'), b.get('action',''),
                b.get('speaker',''), str(b.get('text',''))))
