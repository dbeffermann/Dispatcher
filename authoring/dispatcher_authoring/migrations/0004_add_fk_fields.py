"""
Migration 0004: Add FK references alongside existing text ID fields.

Strategy (safe migration):
  1. Rename conflicting CharField fields (those whose name would clash with
     Django's auto-generated _id column for the new FK):
       GameProject.start_scene_id  → start_scene_str
       Scene.next_scene_id         → next_scene_str
       Case.start_scene_id         → start_scene_str
       DispatchRule.next_scene_id  → next_scene_str

  2. Add new nullable FK fields alongside the now-renamed str fields.

  3. Data migration: populate FK fields by looking up matching objects
     using the existing str values.

  The old *_str fields are kept (editable=False in model) as read-only
  fallback and can be dropped in a future cleanup migration.
"""
from django.db import migrations, models
import django.db.models.deletion


# ---------------------------------------------------------------------------
# Data migration helper
# ---------------------------------------------------------------------------

def populate_fk_fields(apps, schema_editor):
    """Populate FK fields from their legacy text counterparts."""
    GameProject = apps.get_model('dispatcher_authoring', 'GameProject')
    Scene = apps.get_model('dispatcher_authoring', 'Scene')
    Case = apps.get_model('dispatcher_authoring', 'Case')
    ChoiceOption = apps.get_model('dispatcher_authoring', 'ChoiceOption')
    Beat = apps.get_model('dispatcher_authoring', 'Beat')
    OptionBeat = apps.get_model('dispatcher_authoring', 'OptionBeat')
    Vignette = apps.get_model('dispatcher_authoring', 'Vignette')
    CaseField = apps.get_model('dispatcher_authoring', 'CaseField')
    ToolSearch = apps.get_model('dispatcher_authoring', 'ToolSearch')
    DispatchRule = apps.get_model('dispatcher_authoring', 'DispatchRule')
    Contact = apps.get_model('dispatcher_authoring', 'Contact')

    for project in GameProject.objects.all():
        # Build per-project lookup maps
        scene_map = {s.scene_id: s for s in Scene.objects.filter(project=project)}
        vignette_map = {v.vignette_id: v for v in Vignette.objects.filter(project=project)}
        contact_map = {c.contact_id: c for c in Contact.objects.filter(project=project)}

        # Field / tool maps across all cases of this project
        field_map = {}
        tool_map = {}
        for case in Case.objects.filter(project=project):
            for cf in CaseField.objects.filter(case=case):
                field_map[cf.field_id] = cf
            for ts in ToolSearch.objects.filter(case=case):
                tool_map[ts.search_id] = ts

        # ── GameProject.start_scene ──────────────────────────────────────
        if project.start_scene_str and project.start_scene_str in scene_map:
            project.start_scene = scene_map[project.start_scene_str]
            project.save(update_fields=['start_scene'])

        # ── GameProject.wa_default_contact ───────────────────────────────
        if project.whatsapp_default_contact and project.whatsapp_default_contact in contact_map:
            project.wa_default_contact = contact_map[project.whatsapp_default_contact]
            project.save(update_fields=['wa_default_contact'])

        # ── Scene.next_scene ─────────────────────────────────────────────
        scene_updates = []
        for scene in Scene.objects.filter(project=project):
            if scene.next_scene_str and scene.next_scene_str in scene_map:
                scene.next_scene = scene_map[scene.next_scene_str]
                scene_updates.append(scene)
        if scene_updates:
            Scene.objects.bulk_update(scene_updates, ['next_scene'])

        # ── Case.start_scene ─────────────────────────────────────────────
        case_updates = []
        for case in Case.objects.filter(project=project):
            if case.start_scene_str and case.start_scene_str in scene_map:
                case.start_scene = scene_map[case.start_scene_str]
                case_updates.append(case)
        if case_updates:
            Case.objects.bulk_update(case_updates, ['start_scene'])

        # ── ChoiceOption.goto_scene ──────────────────────────────────────
        option_updates = []
        for option in ChoiceOption.objects.filter(choice__scene__project=project):
            if option.goto and option.goto in scene_map:
                option.goto_scene = scene_map[option.goto]
                option_updates.append(option)
        if option_updates:
            ChoiceOption.objects.bulk_update(option_updates, ['goto_scene'])

        # ── Beat FKs ─────────────────────────────────────────────────────
        beat_updates = []
        for beat in Beat.objects.filter(scene__project=project):
            changed = False
            if beat.vignette_ref and beat.vignette_ref in vignette_map:
                beat.vignette = vignette_map[beat.vignette_ref]
                changed = True
            if beat.input_field and beat.input_field in field_map:
                beat.input_case_field = field_map[beat.input_field]
                changed = True
            if beat.tool_ref and beat.tool_ref in tool_map:
                beat.tool_search = tool_map[beat.tool_ref]
                changed = True
            if changed:
                beat_updates.append(beat)
        if beat_updates:
            Beat.objects.bulk_update(beat_updates, ['vignette', 'input_case_field', 'tool_search'])

        # ── OptionBeat FKs ───────────────────────────────────────────────
        obeat_updates = []
        for beat in OptionBeat.objects.filter(option__choice__scene__project=project):
            changed = False
            if beat.vignette_ref and beat.vignette_ref in vignette_map:
                beat.vignette = vignette_map[beat.vignette_ref]
                changed = True
            if beat.input_field and beat.input_field in field_map:
                beat.input_case_field = field_map[beat.input_field]
                changed = True
            if beat.tool_ref and beat.tool_ref in tool_map:
                beat.tool_search = tool_map[beat.tool_ref]
                changed = True
            if changed:
                obeat_updates.append(beat)
        if obeat_updates:
            OptionBeat.objects.bulk_update(obeat_updates, ['vignette', 'input_case_field', 'tool_search'])

        # ── DispatchRule.next_scene ──────────────────────────────────────
        rule_updates = []
        for rule in DispatchRule.objects.filter(case__project=project):
            if rule.next_scene_str and rule.next_scene_str in scene_map:
                rule.next_scene = scene_map[rule.next_scene_str]
                rule_updates.append(rule)
        if rule_updates:
            DispatchRule.objects.bulk_update(rule_updates, ['next_scene'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('dispatcher_authoring', '0003_add_vignette_action'),
    ]

    operations = [
        # ── Step 1: Rename conflicting CharField fields ──────────────────
        migrations.RenameField(
            model_name='gameproject',
            old_name='start_scene_id',
            new_name='start_scene_str',
        ),
        migrations.RenameField(
            model_name='scene',
            old_name='next_scene_id',
            new_name='next_scene_str',
        ),
        migrations.RenameField(
            model_name='case',
            old_name='start_scene_id',
            new_name='start_scene_str',
        ),
        migrations.RenameField(
            model_name='dispatchrule',
            old_name='next_scene_id',
            new_name='next_scene_str',
        ),

        # ── Step 2: Add new FK fields ────────────────────────────────────

        # GameProject.start_scene → Scene
        migrations.AddField(
            model_name='gameproject',
            name='start_scene',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='dispatcher_authoring.scene',
                verbose_name='Start Scene',
            ),
        ),
        # GameProject.wa_default_contact → Contact
        migrations.AddField(
            model_name='gameproject',
            name='wa_default_contact',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='dispatcher_authoring.contact',
                verbose_name='WhatsApp Default Contact',
            ),
        ),
        # Scene.next_scene → Scene (self)
        migrations.AddField(
            model_name='scene',
            name='next_scene',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='previous_scenes', to='dispatcher_authoring.scene',
                verbose_name='Next Scene',
            ),
        ),
        # Scene.case → Case (optional)
        migrations.AddField(
            model_name='scene',
            name='case',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='scenes', to='dispatcher_authoring.case',
                verbose_name='Case (optional)',
            ),
        ),
        # Case.start_scene → Scene
        migrations.AddField(
            model_name='case',
            name='start_scene',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='dispatcher_authoring.scene',
                verbose_name='Start Scene',
            ),
        ),
        # ChoiceOption.goto_scene → Scene
        migrations.AddField(
            model_name='choiceoption',
            name='goto_scene',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='dispatcher_authoring.scene',
                verbose_name='Go To Scene',
            ),
        ),
        # Beat.vignette → Vignette
        migrations.AddField(
            model_name='beat',
            name='vignette',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='dispatcher_authoring.vignette',
                verbose_name='Vignette',
            ),
        ),
        # Beat.input_case_field → CaseField
        migrations.AddField(
            model_name='beat',
            name='input_case_field',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='dispatcher_authoring.casefield',
                verbose_name='Input Field',
            ),
        ),
        # Beat.tool_search → ToolSearch
        migrations.AddField(
            model_name='beat',
            name='tool_search',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='dispatcher_authoring.toolsearch',
                verbose_name='Tool Search',
            ),
        ),
        # OptionBeat.vignette → Vignette
        migrations.AddField(
            model_name='optionbeat',
            name='vignette',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='dispatcher_authoring.vignette',
                verbose_name='Vignette',
            ),
        ),
        # OptionBeat.input_case_field → CaseField
        migrations.AddField(
            model_name='optionbeat',
            name='input_case_field',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='dispatcher_authoring.casefield',
                verbose_name='Input Field',
            ),
        ),
        # OptionBeat.tool_search → ToolSearch
        migrations.AddField(
            model_name='optionbeat',
            name='tool_search',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='dispatcher_authoring.toolsearch',
                verbose_name='Tool Search',
            ),
        ),
        # DispatchRule.next_scene → Scene
        migrations.AddField(
            model_name='dispatchrule',
            name='next_scene',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='dispatcher_authoring.scene',
                verbose_name='Next Scene',
            ),
        ),

        # ── Step 3: Data migration ────────────────────────────────────────
        migrations.RunPython(populate_fk_fields, reverse_code=noop),
    ]
