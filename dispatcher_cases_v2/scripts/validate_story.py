import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
story_path = ROOT / 'data' / 'dispatcher_story.json'
assets_path = ROOT / 'data' / 'dispatcher_assets.json'

story = json.loads(story_path.read_text(encoding='utf-8'))
assets = json.loads(assets_path.read_text(encoding='utf-8'))
errors=[]; warnings=[]
scene_ids={s.get('id') for s in story.get('scenes',[]) if s.get('id')}
case_ids={c.get('id') for c in story.get('cases',[]) if c.get('id')}

if story.get('start') not in scene_ids:
    errors.append(f'start apunta a escena inexistente: {story.get("start")}')

seen=set()
for s in story.get('scenes',[]):
    sid=s.get('id')
    if sid in seen: errors.append(f'escena duplicada: {sid}')
    seen.add(sid)
    if s.get('next') and s['next'] not in scene_ids: errors.append(f'{sid}.next apunta a escena inexistente: {s["next"]}')
    if s.get('case_id') and s['case_id'] not in case_ids: errors.append(f'{sid}.case_id apunta a caso inexistente: {s["case_id"]}')
    for ch in s.get('choices',[]):
        for opt in ch.get('options',[]):
            if opt.get('goto') and opt['goto'] not in scene_ids: errors.append(f'{sid}.choice.goto apunta a escena inexistente: {opt["goto"]}')

vig_keys=set((assets.get('vignettes') or {}).keys())
sfx_keys=set(((assets.get('audio') or {}).get('sfx') or {}).keys())
music_keys=set(((assets.get('audio') or {}).get('music') or {}).keys())

def fields_for(scene):
    c=next((c for c in story.get('cases',[]) if c.get('id')==scene.get('case_id')), None)
    d={}
    d.update(story.get('case_fields') or {})
    if c: d.update(c.get('case_fields') or {})
    return d

def tools_for(scene):
    c=next((c for c in story.get('cases',[]) if c.get('id')==scene.get('case_id')), None)
    d={}
    d.update(story.get('tool_search') or {})
    if c: d.update(c.get('tool_search') or {})
    return d

def walk_beats(beats, scene, ctx):
    fields=fields_for(scene); tools=tools_for(scene)
    for i,b in enumerate(beats or [], start=1):
        kind=b.get('kind')
        where=f'{ctx}.beat[{i}]'
        if kind=='input' and b.get('field') not in fields:
            warnings.append(f'{where}: input field sin case_field: {b.get("field")}')
        if kind=='tool-search' and b.get('tool') not in tools:
            errors.append(f'{where}: tool-search sin definición: {b.get("tool")}')
        if kind=='vignette' and b.get('scene') not in vig_keys:
            warnings.append(f'{where}: vignette sin asset: {b.get("scene")}')
        if kind=='sfx' and b.get('sfx') and b.get('sfx') not in sfx_keys:
            warnings.append(f'{where}: sfx no definido en assets: {b.get("sfx")}')
        if kind=='ambient' and b.get('ambient') and b.get('ambient') not in music_keys:
            warnings.append(f'{where}: ambient no definido en assets: {b.get("ambient")}')

for s in story.get('scenes',[]):
    walk_beats(s.get('beats',[]), s, s.get('id'))
    for ci,ch in enumerate(s.get('choices',[]), start=1):
        for oi,opt in enumerate(ch.get('options',[]), start=1):
            walk_beats(opt.get('beats',[]), s, f'{s.get("id")}.choice[{ci}].option[{oi}]')

print(f'[OK] scenes: {len(scene_ids)}')
print(f'[OK] cases: {len(case_ids)} -> {sorted(case_ids)}')
if warnings:
    print('\nWARNINGS:')
    for w in warnings: print(' -', w)
if errors:
    print('\nERRORS:')
    for e in errors: print(' -', e)
    sys.exit(1)
print('[OK] validación sin errores')
