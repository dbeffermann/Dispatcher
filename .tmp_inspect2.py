import json
with open('dispatcher_cases_v2/data/dispatcher_story.json', encoding='utf-8') as f:
    data = json.load(f)
for s in data['scenes']:
    if s['id'] == 'bienvenida':
        b = s['beats'][0]
        print('beat 0 full:', json.dumps(b, ensure_ascii=False))
