"""
Compara beats DB vs JSON para detectar contenido extra o faltante.
Ejecutar desde: authoring/ con python debug_beats_compare.py
"""
import os, sys, json, django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'authoring_project.settings')
sys.path.insert(0, '.')
django.setup()

from dispatcher_authoring.models import Beat, Scene


def fmt(b):
    kind = b.get('kind', 'dialogue')
    if kind == 'dialogue':
        return f"  DIAL  {b.get('speaker','?')!r}: {b.get('text','')[:70]!r}"
    if kind == 'narration':
        return f"  NARR  {b.get('text','')[:70]!r}"
    if kind == 'vignette':
        action = b.get('action', 'legacy')
        scene = b.get('scene', '')
        text = b.get('text', '')[:40]
        return f"  VIG   action={action!r} scene={scene!r} text={text!r}"
    if kind == 'wa':
        return f"  WA    {b.get('contact','?')} {b.get('speaker','')!r}: {b.get('text','')[:50]!r}"
    if kind == 'tool-search':
        return f"  TOOL  {b.get('tool','?')!r}"
    if kind == 'sfx':
        return f"  SFX   {b.get('sfx','')!r} action={b.get('action','play')!r}"
    if kind == 'ambient':
        return f"  AMBIENT {b.get('ambient','')!r} stop={b.get('stop',False)}"
    if kind == 'input':
        return f"  INPUT field={b.get('field','?')!r}"
    if kind == 'dispatch':
        return "  DISPATCH"
    return f"  {kind}: {b}"


# Load JSON
with open('../dispatcher_cases_v2/data/dispatcher_story.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

json_scenes = {s['id']: s for s in data.get('scenes', [])}

scenes_to_check = ['bienvenida', 'intro', 'supervisor', 'dani_chat',
                   'pre_alert', 'first_alert', 'call_fragment',
                   'manual_search', 'investigation', 'dispatch_choice', 'outro']

for sid in scenes_to_check:
    db_scene = Scene.objects.filter(scene_id=sid).first()
    json_scene = json_scenes.get(sid, {})

    db_beats = [b.to_json() for b in Beat.objects.filter(scene=db_scene).order_by('order')] if db_scene else []
    json_beats = json_scene.get('beats', [])

    match = len(db_beats) == len(json_beats)
    status = "OK" if match else f"MISMATCH DB={len(db_beats)} JSON={len(json_beats)}"

    print(f"\n{'='*60}")
    print(f"  {sid}  [{status}]")
    print(f"{'='*60}")

    max_len = max(len(db_beats), len(json_beats))
    for i in range(max_len):
        db_b = db_beats[i] if i < len(db_beats) else None
        js_b = json_beats[i] if i < len(json_beats) else None

        db_str = fmt(db_b) if db_b else "  <missing>"
        js_str = fmt(js_b) if js_b else "  <missing>"

        same = db_b == js_b if (db_b and js_b) else False
        flag = "  " if same else "!!"

        if not same:
            print(f"  [{i:02d}] {flag} DB  : {db_str.strip()}")
            print(f"        {flag} JSON: {js_str.strip()}")
        # else just show one line if they match
        # (uncomment below to show all):
        # else:
        #     print(f"  [{i:02d}]    {db_str.strip()}")

print("\nDone.")
