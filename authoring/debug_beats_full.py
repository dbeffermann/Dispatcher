"""
Audit detallado de beats problemáticos en la DB.
"""
import os, sys, json, django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'authoring_project.settings')
sys.path.insert(0, '.')
django.setup()

from dispatcher_authoring.models import Beat, Scene

def dump_full(scene_id):
    s = Scene.objects.get(scene_id=scene_id)
    beats = list(Beat.objects.filter(scene=s).order_by('order'))
    print(f"\n{'='*60}")
    print(f"  {scene_id}  (pk={s.id})")
    print(f"{'='*60}")
    for b in beats:
        j = b.to_json()
        print(f"  DB id={b.id} order={b.order} kind={b.kind!r} | {json.dumps(j, ensure_ascii=False)[:110]}")

for sid in ['dispatch_choice', 'call_fragment', 'investigation', 'dani_chat', 'intro', 'supervisor']:
    dump_full(sid)

print("\nDone.")
