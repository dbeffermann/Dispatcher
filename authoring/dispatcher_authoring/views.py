import json
import mimetypes
from collections import deque
from pathlib import Path

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.db import models as _db_models
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_http_methods

from .models import GameProject, Scene, Beat, Vignette, Case, CaseField, Choice, ChoiceOption
from .management.commands.export_dispatcher_json import build_story_json, build_assets_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _admin_url(viewname, pk):
    try:
        return reverse(f'admin:{viewname}', args=[pk])
    except Exception:
        return '#'


def _studio_asset_url(asset_path):
    """Return same-origin preview URL for an asset path under dispatcher_cases_v2/."""
    if not asset_path:
        return ''
    safe_path = str(asset_path).replace('\\', '/').lstrip('/')
    return reverse('studio_asset_file', kwargs={'asset_path': safe_path})


def _scene_studio_dict(scene):
    return {
        'id': scene.id,
        'scene_id': scene.scene_id,
        'title': scene.title,
        'channel': scene.channel,
        'next': scene.effective_next_scene_id,
        'next_scene_id': scene.effective_next_scene_id,
        'objective': scene.objective,
        'case_id': scene.case.case_id if scene.case_id else '',
        'case_pk': scene.case_id,
        'beats': [],
        'choices': [],
        'admin_url': _admin_url('dispatcher_authoring_scene_change', scene.id),
    }


def _choice_studio_dict(choice):
    return {
        'id': choice.id,
        'at_beat': choice.at_beat,
        'prompt': choice.prompt,
        'admin_url': _admin_url('dispatcher_authoring_choice_change', choice.id),
        'options': [_choice_option_studio_dict(opt) for opt in choice.options.order_by('order')],
    }


def _choice_option_studio_dict(opt):
    return {
        'id': opt.id,
        'order': opt.order,
        'label': opt.label,
        'goto': opt.effective_goto,
        'goto_scene_fk': opt.goto_scene_id,
        'goto_scene_id': opt.goto_scene.scene_id if opt.goto_scene_id else opt.goto,
        'admin_url': _admin_url('dispatcher_authoring_choiceoption_change', opt.id),
        'beats': [
            {
                'order': ob.order,
                'kind': ob.kind if ob.kind else 'dialogue',
                'speaker': ob.speaker,
                'text': ob.text,
                'input_field': (ob.input_case_field.field_id if ob.input_case_field_id else ob.input_field) or '',
                'input_case_field_fk': ob.input_case_field_id,
                'input_hint': ob.input_hint,
                'tool_ref': (ob.tool_search.search_id if ob.tool_search_id else ob.tool_ref) or '',
                'vignette_ref': (ob.vignette.vignette_id if ob.vignette_id else ob.vignette_ref) or '',
                'vignette_action': getattr(ob, 'vignette_action', ''),
                'sfx_action': ob.sfx_action,
                'sfx_key': ob.sfx_key,
                'ambient_key': ob.ambient_key,
                'ambient_stop': ob.ambient_stop,
                'wa_contact': ob.wa_contact,
                'media_type': ob.media_type,
                'media_url': ob.media_url,
                'media_caption': ob.media_caption,
            }
            for ob in opt.beats.order_by('order')
        ],
    }


def _infer_start_scene_id(configured_start_id, scenes_map, cases=None):
    """Infer the playable start from graph structure, with stable fallbacks."""
    cases = cases or []
    all_scene_ids = set(scenes_map.keys())
    if not all_scene_ids:
        return ''

    incoming = {sid: 0 for sid in all_scene_ids}
    outgoing = {sid: 0 for sid in all_scene_ids}

    def add_link(from_id, to_id):
        if from_id in all_scene_ids and to_id in all_scene_ids:
            outgoing[from_id] += 1
            incoming[to_id] += 1

    for sid, scene in scenes_map.items():
        add_link(sid, scene.get('next'))
        for choice in scene.get('choices', []):
            for opt in choice.get('options', []):
                add_link(sid, opt.get('goto', ''))

    dispatch_next_by_case = {
        case['case_id']: case['dispatch']['next_scene_id']
        for case in cases
        if case.get('dispatch') and case['dispatch'].get('next_scene_id')
    }
    for sid, scene in scenes_map.items():
        dispatch_next = dispatch_next_by_case.get(scene.get('case_id'))
        if dispatch_next and any(beat.get('kind') == 'dispatch' for beat in scene.get('beats', [])):
            add_link(sid, dispatch_next)

    source_candidates = sorted(
        sid for sid in all_scene_ids
        if outgoing[sid] and incoming[sid] == 0
    )
    if len(source_candidates) == 1:
        return source_candidates[0]
    if configured_start_id in all_scene_ids:
        return configured_start_id
    if source_candidates:
        return source_candidates[0]

    case_starts = [
        case.get('start_scene_id')
        for case in cases
        if case.get('start_scene_id') in all_scene_ids
    ]
    if case_starts:
        return sorted(set(case_starts))[0]
    return sorted(all_scene_ids)[0]


def _compute_flow(start_id, scenes_map, cases=None):
    """Build story flow from narrative links, marking only isolated scenes.

    A scene is only treated as orphan when it has no incoming/outgoing story
    links and is not used as an exported entry point. Scenes outside the main
    project start are still valid if they connect to anything.

    Returns (nodes, edges, orphans) where:
      nodes = [{'id', 'col', 'row', 'orphan'}]
      edges = [{'from', 'to', 'label', 'type'}]
      orphans = [scene_id, ...]
    """
    cases = cases or []
    all_scene_ids = set(scenes_map.keys())
    outgoing = {sid: [] for sid in all_scene_ids}
    incoming = {sid: set() for sid in all_scene_ids}
    edges = []
    edge_keys = set()

    def add_edge(from_id, to_id, label='', edge_type='next'):
        if from_id not in all_scene_ids or to_id not in all_scene_ids:
            return
        key = (from_id, to_id, label or '', edge_type)
        if key in edge_keys:
            return
        edge_keys.add(key)
        outgoing[from_id].append(to_id)
        incoming[to_id].add(from_id)
        edges.append({'from': from_id, 'to': to_id, 'label': label or '', 'type': edge_type})

    for sid, scene in scenes_map.items():
        add_edge(sid, scene.get('next'), '', 'next')
        for choice in scene.get('choices', []):
            for opt in choice.get('options', []):
                add_edge(sid, opt.get('goto', ''), opt.get('label', '->'), 'choice')

    # Dispatch is a narrative transition too. If a scene contains a dispatch
    # beat for a case, connect it to that case's configured dispatch ending.
    dispatch_next_by_case = {
        case['case_id']: case['dispatch']['next_scene_id']
        for case in cases
        if case.get('dispatch') and case['dispatch'].get('next_scene_id')
    }
    for sid, scene in scenes_map.items():
        case_id = scene.get('case_id')
        dispatch_next = dispatch_next_by_case.get(case_id)
        if not dispatch_next:
            continue
        if any(beat.get('kind') == 'dispatch' for beat in scene.get('beats', [])):
            add_edge(sid, dispatch_next, '', 'next')

    entrypoints = []
    if start_id in all_scene_ids:
        entrypoints.append(start_id)
    for case in cases:
        case_start = case.get('start_scene_id')
        if case_start in all_scene_ids and case_start not in entrypoints:
            entrypoints.append(case_start)

    if not entrypoints:
        entrypoints = sorted(
            sid for sid in all_scene_ids
            if outgoing[sid] and not incoming[sid]
        )

    visited = set()
    nodes = []
    col_row = {}  # col -> next available row index

    def add_node(sid, col, orphan=False):
        row = col_row.get(col, 0)
        col_row[col] = row + 1
        nodes.append({'id': sid, 'col': col, 'row': row, 'orphan': orphan})

    def visit_from(starts, start_col=0):
        queue = deque((sid, start_col) for sid in starts if sid in all_scene_ids)
        while queue:
            sid, col = queue.popleft()
            if sid in visited:
                continue
            visited.add(sid)
            add_node(sid, col, orphan=False)
            for nxt in outgoing.get(sid, []):
                if nxt not in visited:
                    queue.append((nxt, col + 1))

    visit_from(entrypoints)

    connected = {
        sid for sid in all_scene_ids
        if outgoing[sid] or incoming[sid] or sid in entrypoints
    }
    max_col = max((n['col'] for n in nodes), default=0) + 2
    for sid in sorted(connected - visited):
        visit_from([sid], max_col)

    orphans = sorted(all_scene_ids - connected)
    max_col = max((n['col'] for n in nodes), default=0) + 2
    for i, oid in enumerate(orphans):
        add_node(oid, max_col, orphan=True)

    return nodes, edges, orphans


def _compute_warnings(scenes_map, cases):
    warnings = []
    all_scene_ids = set(scenes_map.keys())

    # Build global field/tool index across all cases
    all_fields = set()
    all_tools = set()
    for case in cases:
        all_fields.update(f['field_id'] for f in case['fields'])
        all_tools.update(t['search_id'] for t in case['tools'])

    for sid, scene in scenes_map.items():
        # Broken next_scene_id
        nxt = scene.get('next')
        if nxt and nxt not in all_scene_ids:
            warnings.append({
                'type': 'broken_next', 'scene': sid,
                'detail': f'next_scene_id "{nxt}" no existe',
                'admin_url': scene['admin_url'], 'severity': 'error',
            })

        # Beat-level checks
        for beat in scene.get('beats', []):
            kind = beat.get('kind', 'dialogue')
            if kind == 'input':
                fid = beat.get('input_field', '')
                if fid and fid not in all_fields:
                    warnings.append({
                        'type': 'input_no_field', 'scene': sid,
                        'detail': f'Input beat referencia campo "{fid}" inexistente en ningún Case',
                        'admin_url': scene['admin_url'], 'severity': 'error',
                    })
            elif kind == 'tool-search':
                tid = beat.get('tool_ref', '')
                if tid and tid not in all_tools:
                    warnings.append({
                        'type': 'tool_no_search', 'scene': sid,
                        'detail': f'Tool-search referencia "{tid}" inexistente en ningún Case',
                        'admin_url': scene['admin_url'], 'severity': 'error',
                    })

        # Broken choice goto
        for choice in scene.get('choices', []):
            for opt in choice.get('options', []):
                goto = opt.get('goto', '')
                if goto and goto not in all_scene_ids:
                    warnings.append({
                        'type': 'broken_goto', 'scene': sid,
                        'detail': f'Opción "{opt.get("label","")}" apunta a goto "{goto}" que no existe',
                        'admin_url': scene['admin_url'], 'severity': 'error',
                    })

        # Choice at_beat out of range: fires after a beat that doesn't exist
        # Valid range: at_beat <= len(beats) (at_beat==len means "after last beat")
        beat_count = len(scene.get('beats', []))
        for choice in scene.get('choices', []):
            at_b = choice.get('at_beat', 1)
            if at_b > beat_count:
                warnings.append({
                    'type': 'choice_at_beat_range', 'scene': sid,
                    'detail': (
                        f'Choice at_beat={at_b} pero la escena tiene {beat_count} beat(s) '
                        f'— el choice nunca se activará en el runtime'
                    ),
                    'admin_url': scene['admin_url'], 'severity': 'warning',
                })

    # Dispatch without outcomes
    for case in cases:
        if case['dispatch'] is not None and not case['dispatch']['outcomes']:
            warnings.append({
                'type': 'dispatch_no_outcome', 'scene': None, 'case_id': case['case_id'],
                'detail': f'Case "{case["case_id"]}" tiene DispatchRule sin outcomes',
                'admin_url': case['dispatch']['admin_url'], 'severity': 'warning',
            })

    return warnings


# ---------------------------------------------------------------------------
# Context builder (shared between admin and studio views)
# ---------------------------------------------------------------------------

def _build_story_map_context(request):
    """
    Build the context dict for the story map views.
    Returns a dict with story_data, projects, current_project.
    """
    projects = GameProject.objects.all().order_by('name')

    project_id = request.GET.get('project')
    project = projects.filter(pk=project_id).first() if project_id else projects.first()

    if not project:
        return {
            'story_data': None,
            'projects': list(projects.values('id', 'name')),
            'current_project': None,
        }

    # ------------------------------------------------------------------
    # Scenes
    # ------------------------------------------------------------------
    scenes_map = {}
    for scene in (
        project.scenes
        .select_related('next_scene', 'case')
        .prefetch_related(
            'beats__vignette', 'beats__input_case_field', 'beats__tool_search',
            'choices__options__beats__vignette',
            'choices__options__beats__input_case_field',
            'choices__options__beats__tool_search',
            'choices__options__goto_scene',
        )
        .order_by('scene_id')
    ):
        beats_data = [
            {
                'id': b.id,
                'order': b.order,
                'kind': b.kind if b.kind else 'dialogue',
                'speaker': b.speaker,
                'text': b.text,
                'input_field': (b.input_case_field.field_id if b.input_case_field_id else b.input_field) or '',
                'input_case_field_fk': b.input_case_field_id,
                'input_set_value': b.input_set_value,
                'input_answer': b.input_answer,
                'input_hint': b.input_hint,
                'input_error_msg': b.input_error_msg,
                'tool_ref': (b.tool_search.search_id if b.tool_search_id else b.tool_ref) or '',
                'vignette_ref': (b.vignette.vignette_id if b.vignette_id else b.vignette_ref) or '',
                'vignette_fk': b.vignette_id,
                'vignette_action': b.vignette_action,
                'wa_contact': b.wa_contact,
                'media_type': b.media_type,
                'media_url': b.media_url,
                'media_caption': b.media_caption,
                'sfx_action': b.sfx_action,
                'media_name': b.media_name,
                'media_duration': b.media_duration,
                'sfx_key': b.sfx_key,
                'ambient_key': b.ambient_key,
                'ambient_stop': b.ambient_stop,
            }
            for b in scene.beats.order_by('order')
        ]
        choices_data = [_choice_studio_dict(ch) for ch in scene.choices.order_by('at_beat')]
        scenes_map[scene.scene_id] = {
            **_scene_studio_dict(scene),
            'beats': beats_data,
            'choices': choices_data,
        }

    # ------------------------------------------------------------------
    # Cases
    # ------------------------------------------------------------------
    cases = []
    for case in project.cases.select_related('start_scene', 'dispatch_rule__next_scene').prefetch_related('case_fields', 'tool_searches', 'dispatch_rule__outcomes').order_by('case_id'):
        fields_data = [
            {
                'field_id': f.field_id,
                'fact_path': f.fact_path,
                'log_template': f.log_template,
                'notification_label': f.notification_label,
                'id': f.id,
                'admin_url': _admin_url('dispatcher_authoring_casefield_change', f.id),
            }
            for f in case.case_fields.all()
        ]
        tools_data = [
            {
                'search_id': t.search_id,
                'title': t.title,
                'hint': t.hint,
                'id': t.id,
                'admin_url': _admin_url('dispatcher_authoring_toolsearch_change', t.id),
            }
            for t in case.tool_searches.all()
        ]
        dispatch_data = None
        if hasattr(case, 'dispatch_rule'):
            dr = case.dispatch_rule
            dispatch_data = {
                'id': dr.id,
                'next_scene_id': dr.effective_next_scene_id,
                'case_title': dr.case_title,
                'outcomes': [
                    {
                        'outcome_id': o.outcome_id,
                        'match_type': o.match_type,
                        'notification': o.notification,
                    }
                    for o in dr.outcomes.all()
                ],
                'admin_url': _admin_url('dispatcher_authoring_dispatchrule_change', dr.id),
            }
        cases.append({
            'id': case.id,
            'case_id': case.case_id,
            'title': case.title,
            'description': case.description,
            'start_scene_id': case.effective_start_scene_id,
            'fields': fields_data,
            'tools': tools_data,
            'dispatch': dispatch_data,
            'admin_url': _admin_url('dispatcher_authoring_case_change', case.id),
        })

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------
    assets = {
        'vignettes': [
            {
                'vignette_id': v.vignette_id, 'label': v.label,
                'image': v.image, 'subtitle': v.subtitle,
                'image_url': _studio_asset_url(v.image),
                'admin_url': _admin_url('dispatcher_authoring_vignette_change', v.id),
            }
            for v in project.vignettes.all()
        ],
        'audio': [
            {
                'key': a.key, 'asset_type': a.asset_type, 'path': a.path,
                'admin_url': _admin_url('dispatcher_authoring_audioasset_change', a.id),
            }
            for a in project.audio_assets.all()
        ],
        'contacts': [
            {
                'contact_id': c.contact_id, 'name': c.name,
                'admin_url': _admin_url('dispatcher_authoring_contact_change', c.id),
            }
            for c in project.contacts.all()
        ],
    }

    categories = [
        {
            'cat_id': c.cat_id, 'name': c.name,
            'dispatch': c.dispatch, 'order': c.order,
            'admin_url': _admin_url('dispatcher_authoring_manualcategory_change', c.id),
        }
        for c in project.manual_categories.order_by('order')
    ]

    # ------------------------------------------------------------------
    # Flow + Warnings
    # ------------------------------------------------------------------
    project_start_id = _infer_start_scene_id(project.effective_start_scene_id, scenes_map, cases)
    flow_nodes, flow_edges, orphans = _compute_flow(project_start_id, scenes_map, cases)

    warnings = _compute_warnings(scenes_map, cases)
    for oid in orphans:
        warnings.append({
            'type': 'orphan_scene', 'scene': oid,
            'detail': f'Escena "{oid}" está aislada: no tiene entradas ni salidas narrativas',
            'admin_url': scenes_map[oid]['admin_url'] if oid in scenes_map else '#',
            'severity': 'warning',
        })

    data = {
        'project': {
            'id': project.id,
            'name': project.name,
            'start_scene_id': project_start_id,
            'cinema_image': project.cinema_image,
            'cinema_image_url': _studio_asset_url(project.cinema_image),
            'admin_url': _admin_url('dispatcher_authoring_gameproject_change', project.id),
        },
        'scenes': scenes_map,
        'cases': cases,
        'flow': {'nodes': flow_nodes, 'edges': flow_edges, 'orphans': orphans},
        'assets': assets,
        'categories': categories,
        'warnings': warnings,
    }

    return {
        'story_data': data,
        'projects': list(projects.values('id', 'name')),
        'current_project': project,
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@staff_member_required
def story_map_view(request):
    ctx = _build_story_map_context(request)
    return render(request, 'dispatcher_authoring/story_map.html', ctx)


@staff_member_required
def studio_story_map_view(request):
    ctx = _build_story_map_context(request)
    return render(request, 'dispatcher_authoring/studio_story_map.html', ctx)


@staff_member_required
@require_POST
def export_now_view(request):
    """One-click export: writes dispatcher_story.json + dispatcher_assets.json."""
    project_id = request.POST.get('project_id') or request.GET.get('project_id')
    if project_id:
        project = GameProject.objects.filter(pk=project_id).first()
    else:
        project = GameProject.objects.first()
    if not project:
        return JsonResponse({'ok': False, 'error': 'No project found'}, status=400)
    try:
        story  = build_story_json(project)
        assets = build_assets_json(project)
        data_dir = Path(settings.DISPATCHER_DATA_DIR)
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / 'dispatcher_story.json').write_text(
            json.dumps(story,  ensure_ascii=False, indent=2), encoding='utf-8')
        (data_dir / 'dispatcher_assets.json').write_text(
            json.dumps(assets, ensure_ascii=False, indent=2), encoding='utf-8')
        return JsonResponse({
            'ok': True,
            'project': project.name,
            'scenes': len(story.get('scenes', [])),
        })
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


# ===========================================================================
# Studio REST API — powers the in-page visual editor (no page reload)
# ===========================================================================

def _beat_to_api_dict(b):
    """Serialize a Beat instance to a plain dict for the Studio API."""
    return {
        'id': b.id,
        'order': b.order,
        'kind': b.kind,
        'text': b.text,
        'speaker': b.speaker,
        'vignette_fk': b.vignette_id,
        'vignette_ref': (b.vignette.vignette_id if b.vignette_id else b.vignette_ref) or '',
        'vignette_action': b.vignette_action,
        'input_case_field_fk': b.input_case_field_id,
        'input_field': (b.input_case_field.field_id if b.input_case_field_id else b.input_field) or '',
        'input_set_value': b.input_set_value,
        'input_answer': b.input_answer,
        'input_hint': b.input_hint,
        'input_error_msg': b.input_error_msg,
        'tool_ref': (b.tool_search.search_id if b.tool_search_id else b.tool_ref) or '',
        'wa_contact': b.wa_contact,
        'media_type': b.media_type,
        'media_url': b.media_url,
        'media_caption': b.media_caption,
        'media_name': b.media_name,
        'media_duration': b.media_duration,
                'sfx_action': b.sfx_action,
        'sfx_action': b.sfx_action,
        'sfx_key': b.sfx_key,
        'ambient_key': b.ambient_key,
        'ambient_stop': b.ambient_stop,
    }


def _apply_beat_patch(beat, data):
    """Apply JSON patch dict to a Beat instance. Does NOT call save()."""
    STR_FIELDS = [
        'kind', 'text', 'speaker', 'vignette_ref', 'vignette_action',
        'input_field', 'input_set_value', 'input_answer', 'input_hint', 'input_error_msg',
        'tool_ref', 'wa_contact', 'media_type', 'media_url', 'media_caption',
        'media_name', 'media_duration', 'sfx_key', 'sfx_action', 'ambient_key',
    ]
    for f in STR_FIELDS:
        if f in data:
            setattr(beat, f, str(data[f]))
    if beat.sfx_action not in {'play', 'replace', 'stop'}:
        beat.sfx_action = 'play'
    if 'ambient_stop' in data:
        beat.ambient_stop = bool(data['ambient_stop'])
    if 'vignette_fk' in data:
        vid = data['vignette_fk']
        beat.vignette = Vignette.objects.filter(pk=int(vid)).first() if vid else None
    if 'input_case_field_fk' in data:
        fid = data['input_case_field_fk']
        beat.input_case_field = CaseField.objects.filter(pk=int(fid)).first() if fid else None
        if beat.input_case_field_id:
            # Keep legacy field text synchronized for compatibility and warnings.
            beat.input_field = beat.input_case_field.field_id


def _copy_beat_fields(src, dst):
    """Copy all authorable Beat fields from src to dst (except pk/scene/order)."""
    dst.kind = src.kind
    dst.text = src.text
    dst.speaker = src.speaker
    dst.vignette_ref = src.vignette_ref
    dst.vignette_action = src.vignette_action
    dst.input_field = src.input_field
    dst.input_set_value = src.input_set_value
    dst.input_answer = src.input_answer
    dst.input_hint = src.input_hint
    dst.input_error_msg = src.input_error_msg
    dst.tool_ref = src.tool_ref
    dst.vignette_id = src.vignette_id
    dst.input_case_field_id = src.input_case_field_id
    dst.tool_search_id = src.tool_search_id
    dst.wa_contact = src.wa_contact
    dst.media_type = src.media_type
    dst.media_url = src.media_url
    dst.media_caption = src.media_caption
    dst.media_name = src.media_name
    dst.media_duration = src.media_duration
    dst.sfx_key = src.sfx_key
    dst.sfx_action = src.sfx_action
    dst.ambient_key = src.ambient_key
    dst.ambient_stop = src.ambient_stop


def _scene_beats_api_list(scene):
    beats = (
        scene.beats
        .select_related('vignette', 'input_case_field', 'tool_search')
        .order_by('order')
    )
    return [_beat_to_api_dict(b) for b in beats]


@staff_member_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def scene_api_view(request, pk):
    """Studio API: GET scene data / PATCH scene meta fields."""
    scene = Scene.objects.select_related('next_scene', 'project', 'case').filter(pk=pk).first()
    if not scene:
        return JsonResponse({'ok': False, 'error': 'Scene not found'}, status=404)

    if request.method == 'DELETE':
        project = scene.project
        scene_id = scene.scene_id

        # If scene is the project start, clear it so author can re-assign.
        if project.start_scene_id == scene.id or project.start_scene_str == scene_id:
            project.start_scene = None
            project.start_scene_str = ''
            project.save(update_fields=['start_scene', 'start_scene_str'])

        # Clear case starts pointing to this scene.
        Case.objects.filter(project=project, start_scene_id=scene.id).update(start_scene=None, start_scene_str='')
        Case.objects.filter(project=project, start_scene_str=scene_id).update(start_scene_str='')

        # Clear scene-next references (FK and legacy string).
        Scene.objects.filter(project=project, next_scene_id=scene.id).update(next_scene=None, next_scene_str='')
        Scene.objects.filter(project=project, next_scene_str=scene_id).update(next_scene_str='')

        # Clear choice-option gotos (FK and legacy string).
        ChoiceOption.objects.filter(
            choice__scene__project=project,
            goto_scene_id=scene.id,
        ).update(goto_scene=None, goto='')
        ChoiceOption.objects.filter(
            choice__scene__project=project,
            goto=scene_id,
        ).update(goto='')

        scene.delete()
        return JsonResponse({'ok': True, 'deleted_scene_id': scene_id})

    if request.method == 'GET':
        beats = list(
            scene.beats
            .select_related('vignette', 'input_case_field', 'tool_search')
            .order_by('order')
        )
        return JsonResponse({
            'ok': True,
            'scene': {
                'id': scene.id,
                'scene_id': scene.scene_id,
                'title': scene.title,
                'channel': scene.channel,
                'next_scene_id': scene.effective_next_scene_id,
                'objective': scene.objective,
                'case_id': scene.case.case_id if scene.case_id else '',
                'case_pk': scene.case_id,
                'beats': [_beat_to_api_dict(b) for b in beats],
            },
        })

    # PATCH
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    if 'title' in data:
        scene.title = data['title'].strip()
    if 'scene_id' in data:
        new_scene_id = (data['scene_id'] or '').strip()
        if not new_scene_id:
            return JsonResponse({'ok': False, 'error': 'scene_id es obligatorio'}, status=400)
        exists = Scene.objects.filter(project=scene.project, scene_id=new_scene_id).exclude(pk=scene.pk).exists()
        if exists:
            return JsonResponse({'ok': False, 'error': f'La escena "{new_scene_id}" ya existe'}, status=400)
        scene.scene_id = new_scene_id
    if 'channel' in data:
        scene.channel = data['channel']
    if 'objective' in data:
        scene.objective = data['objective']
    if 'case_pk' in data or 'case_id' in data:
        case_ref = data.get('case_pk') or data.get('case_id') or ''
        if case_ref:
            case = None
            try:
                case = Case.objects.filter(project=scene.project, pk=int(case_ref)).first()
            except (TypeError, ValueError):
                case = Case.objects.filter(project=scene.project, case_id=str(case_ref).strip()).first()
            if not case:
                return JsonResponse({'ok': False, 'error': 'Caso no encontrado'}, status=400)
            scene.case = case
        else:
            scene.case = None
    if 'next_scene_id' in data:
        next_id = (data['next_scene_id'] or '').strip()
        if next_id:
            ns = Scene.objects.filter(project=scene.project, scene_id=next_id).first()
            scene.next_scene = ns
            scene.next_scene_str = next_id if not ns else ''
        else:
            scene.next_scene = None
            scene.next_scene_str = ''

    if data.get('set_as_project_start') is True:
        scene.project.start_scene = scene
        scene.project.start_scene_str = scene.scene_id
        scene.project.save(update_fields=['start_scene', 'start_scene_str'])

    scene.save()
    return JsonResponse({
        'ok': True,
        'scene_id': scene.scene_id,
        'next_scene_id': scene.effective_next_scene_id,
        'case_id': scene.case.case_id if scene.case_id else '',
        'case_pk': scene.case_id,
        'project_start_scene_id': scene.project.effective_start_scene_id,
    })


@staff_member_required
@require_POST
def beat_create_view(request, scene_pk):
    """Studio API: create a new Beat at the end of a scene."""
    scene = Scene.objects.filter(pk=scene_pk).first()
    if not scene:
        return JsonResponse({'ok': False, 'error': 'Scene not found'}, status=404)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    max_order = scene.beats.aggregate(_db_models.Max('order'))['order__max']
    beat = Beat(scene=scene, order=(max_order if max_order is not None else -1) + 1)
    _apply_beat_patch(beat, data)
    beat.save()
    beat.refresh_from_db()
    # reload relations for serialization
    beat = Beat.objects.select_related('vignette', 'input_case_field', 'tool_search').get(pk=beat.pk)
    return JsonResponse({'ok': True, 'beat': _beat_to_api_dict(beat)})


@staff_member_required
@require_http_methods(["PATCH", "DELETE"])
def beat_api_view(request, pk):
    """Studio API: update or delete a Beat."""
    beat = Beat.objects.select_related(
        'vignette', 'input_case_field', 'tool_search', 'scene__project'
    ).filter(pk=pk).first()
    if not beat:
        return JsonResponse({'ok': False, 'error': 'Beat not found'}, status=404)

    if request.method == 'DELETE':
        beat.delete()
        return JsonResponse({'ok': True})

    # PATCH
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    _apply_beat_patch(beat, data)
    beat.save()
    beat.refresh_from_db()
    beat = Beat.objects.select_related('vignette', 'input_case_field', 'tool_search').get(pk=beat.pk)
    return JsonResponse({'ok': True, 'beat': _beat_to_api_dict(beat)})


@staff_member_required
@require_POST
def beats_reorder_view(request, scene_pk):
    """Studio API: reorder beats. Body: {"order": [beat_id, ...]}"""
    scene = Scene.objects.filter(pk=scene_pk).first()
    if not scene:
        return JsonResponse({'ok': False, 'error': 'Scene not found'}, status=404)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    order_ids = data.get('order', [])
    beats_by_id = {b.id: b for b in scene.beats.all()}
    for i, bid in enumerate(order_ids):
        b = beats_by_id.get(int(bid))
        if b and b.order != i:
            b.order = i
            b.save(update_fields=['order'])
    return JsonResponse({'ok': True})


@staff_member_required
@require_POST
def beat_transfer_view(request, pk):
    """
    Studio API: copy/move a beat to another scene.
    Body: {"target_scene_id": "scene_x", "mode": "copy"|"move"}
    """
    beat = Beat.objects.select_related(
        'scene__project', 'vignette', 'input_case_field', 'tool_search'
    ).filter(pk=pk).first()
    if not beat:
        return JsonResponse({'ok': False, 'error': 'Beat not found'}, status=404)

    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    mode = (data.get('mode') or 'move').strip().lower()
    if mode not in {'copy', 'move'}:
        return JsonResponse({'ok': False, 'error': 'mode debe ser copy o move'}, status=400)

    target_scene_id = (data.get('target_scene_id') or '').strip()
    if not target_scene_id:
        return JsonResponse({'ok': False, 'error': 'target_scene_id es obligatorio'}, status=400)

    source_scene = beat.scene
    project = source_scene.project
    target_scene = Scene.objects.filter(project=project, scene_id=target_scene_id).first()
    if not target_scene:
        return JsonResponse({'ok': False, 'error': 'Escena destino no encontrada'}, status=404)

    if mode == 'move' and target_scene.id == source_scene.id:
        return JsonResponse({'ok': False, 'error': 'La escena destino debe ser distinta para mover'}, status=400)

    source_order = beat.order

    with transaction.atomic():
        # Insert at end of target by default.
        target_order = target_scene.beats.count()

        # Keep target choices attached to the same old beat boundaries.
        Choice.objects.filter(scene=target_scene, at_beat__gte=target_order).update(
            at_beat=_db_models.F('at_beat') + 1
        )

        if mode == 'copy':
            transferred = Beat(scene=target_scene, order=target_order)
            _copy_beat_fields(beat, transferred)
            transferred.save()
        else:
            # Close the gap in source scene ordering and choice boundaries.
            Beat.objects.filter(scene=source_scene, order__gt=source_order).update(
                order=_db_models.F('order') - 1
            )
            Choice.objects.filter(scene=source_scene, at_beat__gt=source_order).update(
                at_beat=_db_models.F('at_beat') - 1
            )

            beat.scene = target_scene
            beat.order = target_order
            beat.save(update_fields=['scene', 'order'])
            transferred = beat

    transferred = Beat.objects.select_related('vignette', 'input_case_field', 'tool_search').get(pk=transferred.pk)
    source_scene = Scene.objects.get(pk=source_scene.pk)
    target_scene = Scene.objects.get(pk=target_scene.pk)

    return JsonResponse({
        'ok': True,
        'mode': mode,
        'source_scene_id': source_scene.scene_id,
        'target_scene_id': target_scene.scene_id,
        'beat': _beat_to_api_dict(transferred),
        'source_beats': _scene_beats_api_list(source_scene),
        'target_beats': _scene_beats_api_list(target_scene),
    })


@staff_member_required
def vignettes_api_view(request):
    """Studio API: list vignettes for the visual picker."""
    project_id = request.GET.get('project')
    project = (
        GameProject.objects.filter(pk=project_id).first() if project_id
        else GameProject.objects.first()
    )
    if not project:
        return JsonResponse({'ok': False, 'error': 'No project'}, status=404)
    vigs = project.vignettes.all().order_by('vignette_id')
    return JsonResponse({'ok': True, 'vignettes': [
        {
            'id': v.id,
            'vignette_id': v.vignette_id,
            'label': v.label,
            'image': v.image,
            'image_url': _studio_asset_url(v.image),
            'subtitle': v.subtitle,
        }
        for v in vigs
    ]})


@staff_member_required
def scenes_list_api_view(request):
    """Studio API: list all scenes (for next_scene autocomplete)."""
    project_id = request.GET.get('project')
    project = (
        GameProject.objects.filter(pk=project_id).first() if project_id
        else GameProject.objects.first()
    )
    if not project:
        return JsonResponse({'ok': False, 'error': 'No project'}, status=404)
    scenes = list(project.scenes.order_by('scene_id').values('id', 'scene_id', 'title'))
    return JsonResponse({'ok': True, 'scenes': scenes})


@staff_member_required
@require_POST
def scene_create_view(request):
    """Studio API: create a new Scene inside the current project."""
    project_id = request.GET.get('project') or request.POST.get('project_id')
    project = GameProject.objects.filter(pk=project_id).first() if project_id else GameProject.objects.first()
    if not project:
        return JsonResponse({'ok': False, 'error': 'No project'}, status=404)

    try:
        data = json.loads(request.body or '{}')
    except Exception:
        data = {}

    scene_id = (data.get('scene_id') or '').strip()
    title = (data.get('title') or scene_id or 'Nueva escena').strip()
    channel = (data.get('channel') or 'narration').strip() or 'narration'
    objective = (data.get('objective') or '').strip()
    case_id = data.get('case_id')

    if not scene_id:
        return JsonResponse({'ok': False, 'error': 'scene_id es obligatorio'}, status=400)
    if Scene.objects.filter(project=project, scene_id=scene_id).exists():
        return JsonResponse({'ok': False, 'error': f'La escena "{scene_id}" ya existe'}, status=400)

    scene = Scene.objects.create(
        project=project,
        scene_id=scene_id,
        title=title,
        channel=channel,
        objective=objective,
        case_id=case_id or None,
    )
    return JsonResponse({'ok': True, 'scene': _scene_studio_dict(scene)})


@staff_member_required
@require_POST
def scene_choice_create_view(request, scene_pk):
    """Studio API: create a new Choice attached to a scene."""
    scene = Scene.objects.filter(pk=scene_pk).first()
    if not scene:
        return JsonResponse({'ok': False, 'error': 'Scene not found'}, status=404)
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    at_beat = data.get('at_beat')
    try:
        at_beat = int(at_beat)
    except Exception:
        at_beat = scene.beats.count()
    beat_count = scene.beats.count()
    at_beat = max(0, min(beat_count, at_beat))
    prompt = (data.get('prompt') or 'Elige una opción').strip()

    choice = Choice.objects.create(scene=scene, at_beat=at_beat, prompt=prompt)
    return JsonResponse({'ok': True, 'choice': _choice_studio_dict(choice)})


@staff_member_required
@require_http_methods(["PATCH", "DELETE"])
def choice_api_view(request, pk):
    """Studio API: update or delete a Choice."""
    choice = Choice.objects.filter(pk=pk).first()
    if not choice:
        return JsonResponse({'ok': False, 'error': 'Choice not found'}, status=404)

    if request.method == 'DELETE':
        choice.delete()
        return JsonResponse({'ok': True})

    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    if 'at_beat' in data:
        try:
            next_at = int(data['at_beat'])
        except Exception:
            return JsonResponse({'ok': False, 'error': 'at_beat debe ser un entero'}, status=400)
        beat_count = choice.scene.beats.count()
        choice.at_beat = max(0, min(beat_count, next_at))
    if 'prompt' in data:
        choice.prompt = (data['prompt'] or '').strip()
    choice.save()
    return JsonResponse({'ok': True, 'choice': _choice_studio_dict(choice)})


@staff_member_required
@require_POST
def choice_option_create_view(request, choice_pk):
    """Studio API: create an option for a choice, optionally creating its destination scene."""
    choice = Choice.objects.select_related('scene__project').filter(pk=choice_pk).first()
    if not choice:
        return JsonResponse({'ok': False, 'error': 'Choice not found'}, status=404)
    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    label = (data.get('label') or 'Opción').strip()
    goto_scene_id = (data.get('goto_scene_id') or '').strip()
    create_scene = data.get('create_scene') or {}
    goto_scene = None
    created_scene_payload = None

    if create_scene:
        new_scene_id = (create_scene.get('scene_id') or '').strip()
        if not new_scene_id:
            return JsonResponse({'ok': False, 'error': 'scene_id del destino es obligatorio'}, status=400)
        if Scene.objects.filter(project=choice.scene.project, scene_id=new_scene_id).exists():
            return JsonResponse({'ok': False, 'error': f'La escena "{new_scene_id}" ya existe'}, status=400)
        goto_scene = Scene.objects.create(
            project=choice.scene.project,
            scene_id=new_scene_id,
            title=(create_scene.get('title') or new_scene_id).strip(),
            channel=(create_scene.get('channel') or 'narration').strip() or 'narration',
            objective=(create_scene.get('objective') or '').strip(),
            case_id=create_scene.get('case_id') or None,
        )
        created_scene_payload = _scene_studio_dict(goto_scene)
    elif goto_scene_id:
        goto_scene = Scene.objects.filter(project=choice.scene.project, scene_id=goto_scene_id).first()

    max_order = choice.options.aggregate(_db_models.Max('order'))['order__max']
    option = ChoiceOption.objects.create(
        choice=choice,
        order=(max_order if max_order is not None else -1) + 1,
        label=label,
        goto_scene=goto_scene,
        goto='' if goto_scene else goto_scene_id,
    )
    payload = {'ok': True, 'option': _choice_option_studio_dict(option)}
    if created_scene_payload:
        payload['scene'] = created_scene_payload
    return JsonResponse(payload)


@staff_member_required
@require_http_methods(["PATCH", "DELETE"])
def choice_option_api_view(request, pk):
    """Studio API: update or delete a ChoiceOption."""
    option = ChoiceOption.objects.select_related('choice__scene__project', 'goto_scene').filter(pk=pk).first()
    if not option:
        return JsonResponse({'ok': False, 'error': 'ChoiceOption not found'}, status=404)

    if request.method == 'DELETE':
        option.delete()
        return JsonResponse({'ok': True})

    try:
        data = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    if 'label' in data:
        option.label = (data['label'] or '').strip()
    if 'goto_scene_id' in data:
        next_id = (data.get('goto_scene_id') or '').strip()
        if next_id:
            next_scene = Scene.objects.filter(project=option.choice.scene.project, scene_id=next_id).first()
            if not next_scene:
                return JsonResponse({'ok': False, 'error': f'La escena "{next_id}" no existe'}, status=400)
            option.goto_scene = next_scene
            option.goto = ''
        else:
            option.goto_scene = None
            option.goto = ''
    option.save()
    return JsonResponse({'ok': True, 'option': _choice_option_studio_dict(option)})


@staff_member_required
@require_POST
def case_field_create_view(request, case_pk):
    """Studio API: create a CaseField and return it for immediate binding in input beats."""
    case = Case.objects.filter(pk=case_pk).first()
    if not case:
        return JsonResponse({'ok': False, 'error': 'Case not found'}, status=404)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    field_id = (data.get('field_id') or '').strip()
    if not field_id:
        return JsonResponse({'ok': False, 'error': 'field_id es obligatorio'}, status=400)
    if CaseField.objects.filter(case=case, field_id=field_id).exists():
        return JsonResponse({'ok': False, 'error': f'El field_id "{field_id}" ya existe en este Case'}, status=400)

    fact_path = (data.get('fact_path') or f'facts.{field_id}').strip()
    log_template = (data.get('log_template') or f'✔ {field_id}: {{value}}').strip()
    notification_label = (data.get('notification_label') or f'{field_id.upper()} REGISTRADO').strip()

    cf = CaseField.objects.create(
        case=case,
        field_id=field_id,
        fact_path=fact_path,
        log_template=log_template,
        notification_label=notification_label,
    )
    return JsonResponse({'ok': True, 'field': {
        'id': cf.id,
        'field_id': cf.field_id,
        'fact_path': cf.fact_path,
        'log_template': cf.log_template,
        'notification_label': cf.notification_label,
        'admin_url': _admin_url('dispatcher_authoring_casefield_change', cf.id),
    }})


@staff_member_required
def asset_file_view(request, asset_path):
    """Serve game assets from dispatcher_cases_v2/ for studio previews.

    This keeps image previews same-origin under Django and avoids broken relative paths.
    """
    game_root = Path(settings.GAME_DIR).resolve()
    rel_path = str(asset_path).replace('\\', '/').lstrip('/')
    target = (game_root / rel_path).resolve()

    try:
        target.relative_to(game_root)
    except ValueError as exc:
        raise Http404('Invalid asset path') from exc

    if not target.exists() or not target.is_file():
        # Legacy compatibility: some rows still use assets/images/* but files live in assets/vignettes/*
        if rel_path.startswith('assets/images/'):
            legacy_rel = 'assets/vignettes/' + rel_path[len('assets/images/'):]
            legacy_target = (game_root / legacy_rel).resolve()
            try:
                legacy_target.relative_to(game_root)
            except ValueError as exc:
                raise Http404('Invalid asset path') from exc
            if legacy_target.exists() and legacy_target.is_file():
                target = legacy_target
            else:
                raise Http404('Asset not found')
        else:
            raise Http404('Asset not found')

    content_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(open(target, 'rb'), content_type=content_type or 'application/octet-stream')
