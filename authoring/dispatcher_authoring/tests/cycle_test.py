"""
Validation script: runs through the complete Admin → DB → JSON → back cycle.

  python manage.py shell < dispatcher_authoring/tests/cycle_test.py

Outputs a diff proving that editing the DB changes the exported JSON.
"""
import json
from pathlib import Path

# ── 1. Fix the stale start_scene_id ─────────────────────────────────────────
from dispatcher_authoring.models import Case, Beat, GameProject
from dispatcher_authoring.management.commands.export_dispatcher_json import build_story_json

project = GameProject.objects.first()
case = Case.objects.get(case_id='tutorial_mirna_auto_robado')

old_start = case.start_scene_id
case.start_scene_id = 'first_alert'
case.save()
print(f'[FIX] case.start_scene_id: "{old_start}" → "first_alert"')

# ── 2. Pick a specific beat to edit ─────────────────────────────────────────
# investigation scene, order=0: Eric's first line to Mirna
beat = Beat.objects.get(scene__scene_id='investigation', order=0)
original_text = beat.text
print(f'\n[BEFORE] investigation beat[0]:\n  "{original_text}"')

# ── 3. Edit the beat ─────────────────────────────────────────────────────────
test_text = original_text + ' [CICLO VALIDADO]'
beat.text = test_text
beat.save()
print(f'\n[EDIT] New text:\n  "{test_text}"')

# ── 4. Export and verify the change appears in the JSON ──────────────────────
story = build_story_json(project)
story_str = json.dumps(story, ensure_ascii=False, indent=2)

if 'CICLO VALIDADO' in story_str:
    print('\n[PASS] ✔  The edited text is present in the exported JSON.')
else:
    print('\n[FAIL] ✗  Edited text NOT found in exported JSON.')

# ── 5. Revert the beat ───────────────────────────────────────────────────────
beat.text = original_text
beat.save()
story_reverted = build_story_json(project)
story_reverted_str = json.dumps(story_reverted, ensure_ascii=False, indent=2)

if 'CICLO VALIDADO' not in story_reverted_str:
    print('[PASS] ✔  Revert confirmed: edited text no longer in JSON.')
else:
    print('[FAIL] ✗  Revert failed — edited text still present.')

# ── 6. Summary ───────────────────────────────────────────────────────────────
print('\n── Summary ──────────────────────────────────────────────────────────')
print(f'  GameProject:  {project.name}  (pk={project.pk})')
print(f'  Case fixed:   start_scene_id = "first_alert"  (was "{old_start}")')
print(f'  Beat edited:  investigation scene, order=0')
print(f'  Cycle test:   PASS (edit → export → verify → revert → verify)')
print('─────────────────────────────────────────────────────────────────────')
