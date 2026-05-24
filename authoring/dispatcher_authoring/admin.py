from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from django.urls import reverse, path

from .models import (
    AbstractBeat,
    GameProject,
    ManualCategory,
    Vignette,
    AudioAsset,
    Contact,
    Scene,
    Beat,
    Choice,
    ChoiceOption,
    OptionBeat,
    Case,
    CaseField,
    ToolSearch,
    DispatchRule,
    DispatchOutcome,
)

# ---------------------------------------------------------------------------
# Beat admin form — shared validation for Beat and OptionBeat
# ---------------------------------------------------------------------------

class BeatAdminForm(forms.ModelForm):
    """Validates vignette-related fields; works for both Beat and OptionBeat."""

    def clean(self):
        data = super().clean()
        kind = data.get('kind', '')
        if kind != AbstractBeat.VIGNETTE:
            return data
        action = data.get('vignette_action', '') or ''
        vignette_fk  = data.get('vignette')
        vignette_ref = (data.get('vignette_ref') or '').strip()
        # open and change (and legacy '') require an image source
        if action in ('open', 'change', ''):
            if not vignette_fk and not vignette_ref:
                raise ValidationError({
                    'vignette': (
                        'Requerido para action=open/change: '
                        'selecciona una Vignette del dropdown.'
                    )
                })
        return data


# ---------------------------------------------------------------------------
# Site customisation
# ---------------------------------------------------------------------------

admin.site.site_header = 'Dispatcher Authoring'
admin.site.site_title = 'Dispatcher Admin'
admin.site.index_title = format_html(
    'Gestión de contenido &nbsp;·&nbsp; '
    '<a href="/admin/dispatcher_authoring/story-map/" '
    'style="background:#3b82f6;color:#fff;padding:4px 12px;border-radius:5px;font-size:12px;text-decoration:none;font-weight:700;">'
    '🗺 Story Map</a>'
)


# ---------------------------------------------------------------------------
# Beat inline — shared base class
# ---------------------------------------------------------------------------

BEAT_FIELDSETS = (
    (None, {
        'fields': ('order', 'kind'),
    }),
    ('� Texto / Subtítulo', {
        'fields': ('text',),
        'classes': ('beat-group-text',),
        'description': (
            '<strong>Narración:</strong> texto del narrador. '
            '<strong>Diálogo:</strong> réplica del personaje. '
            '<strong>Vignette open / text / change:</strong> '
            '<em>subtítulo visible sobre la imagen en pantalla</em> '
            '(se muestra como caption en la cinemática).'
        ),
    }),
    ('🗣️ Diálogo', {
        'fields': ('speaker',),
        'classes': ('beat-group-dialogue',),
        'description': 'Nombre del personaje que habla. Solo para beats de diálogo (kind vacío).',
    }),
    ('🎬 Vignette', {
        'fields': ('vignette_action', 'vignette', 'vignette_ref'),
        'classes': ('beat-group-vignette', 'collapse'),
        'description': (
            '<strong>Flujo de acciones:</strong> '
            '<em>open</em> → abre plano (imagen + subtítulo opcional desde Texto); '
            '<em>text</em> → actualiza subtítulo (sin cambiar imagen); '
            '<em>change</em> → cambia imagen + subtítulo opcional; '
            '<em>close</em> → cierra overlay (sin Vignette ni Texto). '
            '<br><strong>Vignette:</strong> selecciona del dropdown. '
            'Solo rellena "Vignette Ref" si no encuentras la viñeta en el dropdown (campo legado).'
        ),
    }),
    ('📋 Input', {
        'fields': ('input_case_field', 'input_set_value', 'input_answer', 'input_hint', 'input_field'),
        'classes': ('beat-group-input', 'collapse'),
        'description': 'Selecciona el campo del caso desde el dropdown.',
    }),
    ('🔍 Tool Search', {
        'fields': ('tool_search', 'tool_ref'),
        'classes': ('beat-group-tool', 'collapse'),
        'description': 'Selecciona la herramienta de búsqueda desde el dropdown.',
    }),
    ('📱 WhatsApp (wa)', {
        'fields': ('wa_contact', 'media_type', 'media_url', 'media_caption', 'media_name', 'media_duration'),
        'classes': ('beat-group-wa', 'collapse'),
    }),
    ('🔊 Audio', {
        'fields': ('sfx_key', 'sfx_action', 'ambient_key', 'ambient_stop'),
        'classes': ('beat-group-audio', 'collapse'),
        'description': 'sfx_key/sfx_action → sonido puntual con acción explícita. ambient_key → música de ambiente.',
    }),
)


class BeatInlineBase(admin.StackedInline):
    extra = 0
    ordering = ['order']
    show_change_link = True
    form = BeatAdminForm
    fieldsets = BEAT_FIELDSETS
    autocomplete_fields = ['vignette', 'input_case_field', 'tool_search']

    class Media:
        js = ('dispatcher_authoring/js/beat_inline.js',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Limit FK choices to the same project when possible."""
        project = self._get_parent_project(request)
        if project:
            if db_field.name == 'vignette':
                kwargs['queryset'] = Vignette.objects.filter(project=project).order_by('vignette_id')
            elif db_field.name == 'input_case_field':
                kwargs['queryset'] = CaseField.objects.filter(
                    case__project=project
                ).select_related('case').order_by('case__case_id', 'field_id')
            elif db_field.name == 'tool_search':
                kwargs['queryset'] = ToolSearch.objects.filter(
                    case__project=project
                ).select_related('case').order_by('case__case_id', 'search_id')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def _get_parent_project(self, request):
        """Try to resolve the parent object's project from the URL."""
        pk = request.resolver_match.kwargs.get('object_id')
        if not pk:
            return None
        # This base class is used in BeatInline (parent=Scene) and OptionBeatInline (parent=ChoiceOption)
        # We determine which by checking the model
        try:
            if hasattr(self, 'parent_model') and self.parent_model:
                obj = self.parent_model.objects.select_related('project').get(pk=pk)
                return getattr(obj, 'project', None)
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# GameProject inlines
# ---------------------------------------------------------------------------

class ManualCategoryInline(admin.TabularInline):
    model = ManualCategory
    extra = 0
    fields = ('order', 'cat_id', 'name', 'dispatch')
    ordering = ['order']
    show_change_link = True


class VignetteInline(admin.TabularInline):
    model = Vignette
    extra = 0
    fields = ('vignette_id', 'image', 'label', 'subtitle')
    show_change_link = True


class AudioAssetInline(admin.TabularInline):
    model = AudioAsset
    extra = 0
    fields = ('asset_type', 'key', 'path')


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 0
    fields = ('contact_id', 'name', 'extra_data')
    show_change_link = True


# ---------------------------------------------------------------------------
# GameProject admin
# ---------------------------------------------------------------------------

@admin.register(GameProject)
class GameProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'start_scene', 'wa_default_contact', 'updated_at')
    autocomplete_fields = ['start_scene', 'wa_default_contact']
    fieldsets = (
        ('Proyecto', {
            'fields': ('name', 'start_scene'),
        }),
        ('Cinema / Pantalla de apertura', {
            'fields': ('cinema_image', 'cinema_studio', 'cinema_title', 'cinema_place', 'cinema_text'),
            'classes': ('collapse',),
        }),
        ('WhatsApp', {
            'fields': ('wa_default_contact',),
            'classes': ('collapse',),
        }),
    )
    inlines = [ManualCategoryInline, VignetteInline, AudioAssetInline, ContactInline]


# ---------------------------------------------------------------------------
# ManualCategory standalone admin
# ---------------------------------------------------------------------------

@admin.register(ManualCategory)
class ManualCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'cat_id', 'project', 'dispatch', 'order')
    list_filter = ('project',)
    search_fields = ('cat_id', 'name')
    fieldsets = (
        (None, {
            'fields': ('project', 'order', 'cat_id', 'name', 'dispatch'),
        }),
        ('Protocolo', {
            'fields': ('when', 'keys', 'steps', 'avoid'),
        }),
    )


# ---------------------------------------------------------------------------
# Vignette standalone admin
# ---------------------------------------------------------------------------

@admin.register(Vignette)
class VignetteAdmin(admin.ModelAdmin):
    list_display = ('vignette_id', 'label', 'image', 'project')
    list_filter = ('project',)
    search_fields = ('vignette_id', 'label')


# ---------------------------------------------------------------------------
# Scene — beats and choices inlines
# ---------------------------------------------------------------------------

class BeatInline(BeatInlineBase):
    model = Beat
    verbose_name = 'Beat'
    verbose_name_plural = 'Beats'
    parent_model = Scene

    def _get_parent_project(self, request):
        pk = request.resolver_match.kwargs.get('object_id')
        if not pk:
            return None
        try:
            return Scene.objects.select_related('project').get(pk=pk).project
        except Scene.DoesNotExist:
            return None


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0
    fields = ('at_beat', 'prompt')
    show_change_link = True


@admin.register(Scene)
class SceneAdmin(admin.ModelAdmin):
    list_display = ('scene_id', 'title', 'channel', 'next_scene', 'case', 'project', 'emergency_badge')
    list_filter = ('project', 'channel', 'case')
    search_fields = ('scene_id', 'title')
    autocomplete_fields = ['next_scene', 'case']
    fieldsets = (
        (None, {
            'fields': ('project', 'scene_id', 'title', 'objective'),
        }),
        ('Flujo', {
            'fields': ('channel', 'next_scene', 'case'),
            'description': (
                'next_scene → escena siguiente (dropdown). '
                'case → asociar esta escena a un caso (habilita filtros de inputs/tools).'
            ),
        }),
    )
    inlines = [BeatInline, ChoiceInline]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'next_scene':
            pk = request.resolver_match.kwargs.get('object_id')
            if pk:
                try:
                    proj = Scene.objects.select_related('project').get(pk=pk).project
                    kwargs['queryset'] = Scene.objects.filter(project=proj).order_by('scene_id')
                except Scene.DoesNotExist:
                    pass
        elif db_field.name == 'case':
            pk = request.resolver_match.kwargs.get('object_id')
            if pk:
                try:
                    proj = Scene.objects.select_related('project').get(pk=pk).project
                    kwargs['queryset'] = Case.objects.filter(project=proj).order_by('case_id')
                except Scene.DoesNotExist:
                    pass
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description='911?', boolean=True)
    def emergency_badge(self, obj):
        return obj.is_emergency()


# ---------------------------------------------------------------------------
# Choice — with option beats inline
# ---------------------------------------------------------------------------

class OptionBeatInline(BeatInlineBase):
    model = OptionBeat
    verbose_name = 'Beat de opción'
    verbose_name_plural = 'Beats de la opción'

    def _get_parent_project(self, request):
        pk = request.resolver_match.kwargs.get('object_id')
        if not pk:
            return None
        try:
            return ChoiceOption.objects.select_related(
                'choice__scene__project'
            ).get(pk=pk).choice.scene.project
        except ChoiceOption.DoesNotExist:
            return None


class ChoiceOptionInline(admin.TabularInline):
    model = ChoiceOption
    extra = 0
    fields = ('order', 'label', 'goto_scene')
    autocomplete_fields = ['goto_scene']
    show_change_link = True

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'goto_scene':
            pk = request.resolver_match.kwargs.get('object_id')
            if pk:
                try:
                    proj = Choice.objects.select_related(
                        'scene__project'
                    ).get(pk=pk).scene.project
                    kwargs['queryset'] = Scene.objects.filter(project=proj).order_by('scene_id')
                except Choice.DoesNotExist:
                    pass
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Choice)
class ChoiceAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'scene', 'at_beat', 'prompt')
    list_filter = ('scene__project',)
    search_fields = ('prompt', 'scene__scene_id')
    inlines = [ChoiceOptionInline]


@admin.register(ChoiceOption)
class ChoiceOptionAdmin(admin.ModelAdmin):
    list_display = ('label', 'choice', 'goto_scene', 'order')
    list_filter = ('choice__scene__project',)
    search_fields = ('label',)
    autocomplete_fields = ['goto_scene']
    inlines = [OptionBeatInline]


# ---------------------------------------------------------------------------
# Case inlines
# ---------------------------------------------------------------------------

class CaseFieldInline(admin.TabularInline):
    model = CaseField
    extra = 0
    fields = ('field_id', 'fact_path', 'log_template', 'notification_label')
    verbose_name = 'Campo de caso'
    verbose_name_plural = 'Campos de caso'


class ToolSearchInline(admin.TabularInline):
    model = ToolSearch
    extra = 0
    fields = ('search_id', 'title', 'hint', 'placeholder', 'match_patterns')
    show_change_link = True
    verbose_name = 'Herramienta de búsqueda'
    verbose_name_plural = 'Herramientas de búsqueda'


class DispatchRuleInline(admin.StackedInline):
    model = DispatchRule
    extra = 0
    max_num = 1
    fields = ('case_title', 'available_units', 'required_units', 'next_scene')
    autocomplete_fields = ['next_scene']
    show_change_link = True
    verbose_name = 'Regla de despacho'
    verbose_name_plural = 'Regla de despacho'


# ---------------------------------------------------------------------------
# Case admin
# ---------------------------------------------------------------------------

@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ('title', 'case_id', 'project', 'start_scene', 'field_count', 'has_dispatch')
    list_filter = ('project',)
    search_fields = ('case_id', 'title')
    autocomplete_fields = ['start_scene']
    fieldsets = (
        (None, {
            'fields': ('project', 'case_id', 'title', 'description', 'start_scene'),
        }),
    )
    inlines = [CaseFieldInline, ToolSearchInline, DispatchRuleInline]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'start_scene':
            pk = request.resolver_match.kwargs.get('object_id')
            if pk:
                try:
                    proj = Case.objects.select_related('project').get(pk=pk).project
                    kwargs['queryset'] = Scene.objects.filter(project=proj).order_by('scene_id')
                except Case.DoesNotExist:
                    pass
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description='Campos')
    def field_count(self, obj):
        return obj.case_fields.count()

    @admin.display(description='Despacho', boolean=True)
    def has_dispatch(self, obj):
        return hasattr(obj, 'dispatch_rule')


# ---------------------------------------------------------------------------
# CaseField standalone (for detailed editing)
# ---------------------------------------------------------------------------

@admin.register(CaseField)
class CaseFieldAdmin(admin.ModelAdmin):
    list_display = ('field_id', 'case', 'fact_path', 'notification_label')
    list_filter = ('case__project', 'case')
    search_fields = ('field_id', 'fact_path')


# ---------------------------------------------------------------------------
# ToolSearch standalone (for editing result_data)
# ---------------------------------------------------------------------------

@admin.register(ToolSearch)
class ToolSearchAdmin(admin.ModelAdmin):
    list_display = ('search_id', 'title', 'case')
    list_filter = ('case__project', 'case')
    search_fields = ('search_id', 'title')
    fieldsets = (
        (None, {
            'fields': ('case', 'search_id', 'title', 'hint', 'placeholder', 'match_patterns'),
        }),
        ('Resultado / Catálogo', {
            'fields': ('result_data',),
            'description': (
                'Para búsqueda de mapa: {"result": {"name": "...", "address": "...", ...}}<br>'
                'Para catálogo: {"models": [{"name": "...", "type": "...", ...}]}'
            ),
        }),
    )


# ---------------------------------------------------------------------------
# DispatchRule + DispatchOutcome admin
# ---------------------------------------------------------------------------

class DispatchOutcomeInline(admin.TabularInline):
    model = DispatchOutcome
    extra = 0
    fields = ('outcome_id', 'match_type', 'match_units', 'notification', 'beats_json')
    show_change_link = True
    verbose_name = 'Outcome de despacho'
    verbose_name_plural = 'Outcomes de despacho'


@admin.register(DispatchRule)
class DispatchRuleAdmin(admin.ModelAdmin):
    list_display = ('case_title', 'case', 'next_scene', 'outcome_count')
    list_filter = ('case__project',)
    search_fields = ('case_title', 'case__case_id')
    autocomplete_fields = ['next_scene']
    fieldsets = (
        (None, {
            'fields': ('case', 'case_title', 'next_scene'),
        }),
        ('Unidades', {
            'fields': ('available_units', 'required_units'),
            'description': (
                'available_units: [{"id": "Carabineros", "icon": "\U0001f694", "desc": "Patrulla"}]<br>'
                'required_units: ["Carabineros", "Seguridad Ciudadana"]'
            ),
        }),
    )
    inlines = [DispatchOutcomeInline]

    @admin.display(description='Outcomes')
    def outcome_count(self, obj):
        return obj.outcomes.count()


@admin.register(DispatchOutcome)
class DispatchOutcomeAdmin(admin.ModelAdmin):
    list_display = ('outcome_id', 'notification', 'match_type', 'rule')
    list_filter = ('rule__case__project',)
    search_fields = ('outcome_id', 'notification')
    fieldsets = (
        (None, {
            'fields': ('rule', 'outcome_id', 'notification'),
        }),
        ('Condición de match', {
            'fields': ('match_type', 'match_units'),
            'description': (
                'match_type="all_required" → activa cuando se despachan todas las unidades requeridas.<br>'
                'match_type="" + match_units=["Carabineros"] → activa cuando solo esa unidad fue despachada.'
            ),
        }),
        ('Beats', {
            'fields': ('beats_json',),
            'description': (
                'Beats en formato JSON. Ej: '
                '[{"kind": "narration", "text": "..."}, {"speaker": "ERIC", "text": "..."}]'
            ),
        }),
    )


# ---------------------------------------------------------------------------
# AudioAsset standalone
# ---------------------------------------------------------------------------

@admin.register(AudioAsset)
class AudioAssetAdmin(admin.ModelAdmin):
    list_display = ('key', 'asset_type', 'path', 'project')
    list_filter = ('project', 'asset_type')
    search_fields = ('key', 'path')


# ---------------------------------------------------------------------------
# Contact standalone
# ---------------------------------------------------------------------------

@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ('contact_id', 'name', 'project')
    list_filter = ('project',)
    search_fields = ('contact_id', 'name')
