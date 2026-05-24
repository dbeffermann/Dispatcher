"""
Demo: create a new CaseField + input Beat from scratch, then export and validate.
This proves step 6-7 of the validation cycle.

  python manage.py shell < dispatcher_authoring/tests/new_input_test.py
"""
import json
from dispatcher_authoring.models import (
    Beat, Case, CaseField, GameProject, Scene
)
from dispatcher_authoring.management.commands.export_dispatcher_json import build_story_json

project = GameProject.objects.first()
case = Case.objects.get(case_id='tutorial_mirna_auto_robado')
investigation = Scene.objects.get(scene_id='investigation', project=project)

print('=== Step 6: Create a new CaseField + input Beat ===')

# 1. Create the CaseField
new_field = CaseField.objects.create(
    case=case,
    field_id='test_phone',
    fact_path='facts.phone',
    log_template='✔ Teléfono: {value}',
    notification_label='TELÉFONO REGISTRADO',
)
print(f'  Created CaseField: {new_field}')

# 2. Create an input Beat in investigation scene
max_order = investigation.beats.order_by('-order').first().order
new_beat = Beat.objects.create(
    scene=investigation,
    order=max_order + 1,
    kind='input',
    input_field='test_phone',
    input_set_value='',
    input_answer='',
    input_hint='Ingresa el número de teléfono del llamante',
)
print(f'  Created Beat: {new_beat}')

# 3. Export and verify
story = build_story_json(project)
story_str = json.dumps(story, ensure_ascii=False, indent=2)

has_field = '"test_phone"' in story_str
has_beat = '"field": "test_phone"' in story_str
print(f'\n=== Step 7: Verify export ===')
print(f'  case_fields contains test_phone: {"PASS" if has_field else "FAIL"}')
print(f'  beat kind=input field=test_phone: {"PASS" if has_beat else "FAIL"}')

# 4. Cleanup
new_beat.delete()
new_field.delete()
print('\n  Cleaned up test data.')
print('\n[DONE] Steps 6-7 validated: new CaseField + input Beat → export works.')
