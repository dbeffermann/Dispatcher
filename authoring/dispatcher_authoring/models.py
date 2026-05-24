from django.db import models


# ---------------------------------------------------------------------------
# Shared beat serialization helpers (used by models and management commands)
# ---------------------------------------------------------------------------

class AbstractBeat(models.Model):
    """
    Base class for both Scene beats and ChoiceOption beats.
    kind='' means a dialogue beat (speaker + text), omits the 'kind' key in JSON.
    """
    DIALOGUE = ''
    NARRATION = 'narration'
    VIGNETTE = 'vignette'
    INPUT = 'input'
    TOOL_SEARCH = 'tool-search'
    WA = 'wa'
    SFX = 'sfx'
    AMBIENT = 'ambient'
    DISPATCH = 'dispatch'

    KIND_CHOICES = [
        (DIALOGUE, 'Dialogue (speaker)'),
        (NARRATION, 'Narration'),
        (VIGNETTE, 'Vignette / Cinemática'),
        (INPUT, 'Input'),
        (TOOL_SEARCH, 'Tool Search'),
        (WA, 'WhatsApp Media (wa)'),
        (SFX, 'SFX Trigger'),
        (AMBIENT, 'Ambient Trigger'),
        (DISPATCH, 'Dispatch'),
    ]

    VIGNETTE_ACTION_CHOICES = [
        ('', 'Legacy (auto-close tras timer)'),
        ('open', 'open — abrir plano (imagen persiste)'),
        ('text', 'text — actualizar texto (imagen persiste)'),
        ('change', 'change — cambiar plano/imagen'),
        ('close', 'close — cerrar viñeta, volver al juego'),
    ]

    SFX_ACTION_CHOICES = [
        ('play', 'play — iniciar / superponer'),
        ('replace', 'replace — detener SFX activos y reproducir este'),
        ('stop', 'stop — detener SFX activos'),
    ]

    order = models.PositiveIntegerField(default=0, help_text='Beat order within its parent (0-based)')
    kind = models.CharField(
        max_length=20, blank=True, choices=KIND_CHOICES, default='',
        help_text='Leave blank for a dialogue beat with a speaker',
    )
    text = models.TextField(blank=True)
    speaker = models.CharField(
        max_length=200, blank=True,
        help_text='Speaker name — only for Dialogue beats',
    )

    # vignette kind
    vignette_ref = models.CharField(
        max_length=100, blank=True,
        help_text='Vignette ID (asset key) — for vignette beats (e.g. "office"). '
                  'Required for action=open/change; leave blank for action=text/close.',
    )
    vignette_action = models.CharField(
        max_length=20, blank=True, choices=VIGNETTE_ACTION_CHOICES, default='',
        help_text='Acción cinematográfica: open → abrir plano; text → actualizar texto; '
                  'change → cambiar plano; close → cerrar viñeta. '
                  'Dejar vacío para comportamiento legacy (auto-close).',
    )

    # input kind
    input_field = models.CharField(
        max_length=100, blank=True,
        help_text='CaseField ID — only for Input beats (e.g. "plate")',
    )
    input_set_value = models.CharField(
        max_length=500, blank=True,
        help_text='setValue — pre-filled value for the input',
    )
    input_answer = models.CharField(
        max_length=500, blank=True,
        help_text='answer — accepted answer string',
    )
    input_hint = models.TextField(blank=True, help_text='hint — shown to player')
    input_error_msg = models.CharField(
        max_length=500, blank=True,
        help_text='Custom error message when answer does not match (optional). '
                  'If blank, a default message is shown.',
    )

    # tool-search kind
    tool_ref = models.CharField(
        max_length=100, blank=True,
        help_text='ToolSearch ID (legacy text). Prefer tool_search FK below.',
    )

    # ------ FK references (nullable — preferred over text fields above) ------
    vignette = models.ForeignKey(
        'Vignette', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Vignette',
        help_text='Select from dropdown — preferred over Vignette Ref text field.',
    )
    input_case_field = models.ForeignKey(
        'CaseField', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Input Field',
        help_text='Select from dropdown — preferred over Input Field text field.',
    )
    tool_search = models.ForeignKey(
        'ToolSearch', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Tool Search',
        help_text='Select from dropdown — preferred over Tool Ref text field.',
    )

    # wa kind (multimedia message)
    wa_contact = models.CharField(
        max_length=100, blank=True,
        help_text='WA contact ID (e.g. "dani") — only for wa beats',
    )
    media_type = models.CharField(
        max_length=20, blank=True,
        help_text='image | voice | file — only for wa beats',
    )
    media_url = models.CharField(
        max_length=500, blank=True,
        help_text='Media URL/path — image/file source',
    )
    media_caption = models.TextField(blank=True, help_text='Caption for image/file media')
    media_name = models.CharField(max_length=200, blank=True, help_text='File display name')
    media_duration = models.CharField(max_length=20, blank=True, help_text='Voice note duration (e.g. 0:12)')

    # sfx / ambient kinds
    sfx_key = models.CharField(max_length=100, blank=True, help_text='SFX key (e.g. notif)')
    sfx_action = models.CharField(
        max_length=20, blank=True, choices=SFX_ACTION_CHOICES, default='play',
        help_text='play → superpone; replace → detiene SFX activos y reproduce este; '
                  'stop → corta los SFX activos.',
    )
    ambient_key = models.CharField(max_length=100, blank=True, help_text='Ambient key (e.g. rain)')
    ambient_stop = models.BooleanField(default=False, help_text='Set true to stop ambient audio')

    def to_json(self):
        """Serialize this beat back to the JSON structure expected by dispatcher_console.html."""
        if self.kind == self.NARRATION:
            return {'kind': 'narration', 'text': self.text}
        if self.kind == self.VIGNETTE:
            d = {'kind': 'vignette'}
            if self.vignette_action:
                d['action'] = self.vignette_action
            vig_id = self.vignette.vignette_id if self.vignette_id else self.vignette_ref
            if vig_id:
                d['scene'] = vig_id
            if self.text and self.vignette_action in ('text', 'open'):
                d['text'] = self.text
            return d
        if self.kind == self.DISPATCH:
            return {'kind': 'dispatch'}
        if self.kind == self.INPUT:
            field_id = self.input_case_field.field_id if self.input_case_field_id else self.input_field
            d = {'kind': 'input', 'field': field_id}
            if self.input_set_value:
                d['setValue'] = self.input_set_value
            if self.input_answer:
                d['answer'] = self.input_answer
            if self.input_hint:
                d['hint'] = self.input_hint
            if self.input_error_msg:
                d['errorMsg'] = self.input_error_msg
            return d
        if self.kind == self.TOOL_SEARCH:
            tool_id = self.tool_search.search_id if self.tool_search_id else self.tool_ref
            return {'kind': 'tool-search', 'tool': tool_id}
        if self.kind == self.WA:
            d = {
                'kind': 'wa',
                'contact': self.wa_contact or 'dani',
                'speaker': self.speaker,
                'text': self.text,
            }
            if self.media_type:
                d['mediaType'] = self.media_type
            if self.media_url:
                d['url'] = self.media_url
            if self.media_caption:
                d['caption'] = self.media_caption
            if self.media_name:
                d['name'] = self.media_name
            if self.media_duration:
                d['dur'] = self.media_duration
            return d
        if self.kind == self.SFX:
            action = self.sfx_action or 'play'
            d = {'kind': 'sfx'}
            if action != 'play':
                d['action'] = action
            if action != 'stop':
                d['sfx'] = self.sfx_key or 'notif'
            return d
        if self.kind == self.AMBIENT:
            d = {'kind': 'ambient'}
            if self.ambient_stop:
                d['stop'] = True
            elif self.ambient_key:
                d['ambient'] = self.ambient_key
            return d
        # Dialogue (kind == '')
        d = {'speaker': self.speaker, 'text': self.text}
        if self.media_type:
            d['mediaType'] = self.media_type
        if self.media_url:
            d['url'] = self.media_url
        if self.media_caption:
            d['caption'] = self.media_caption
        if self.media_name:
            d['name'] = self.media_name
        if self.media_duration:
            d['dur'] = self.media_duration
        return d

    class Meta:
        abstract = True
        ordering = ['order']

    def __str__(self):
        if self.kind == self.NARRATION:
            return f'[{self.order}] narration: {self.text[:60]}'
        if self.kind == self.VIGNETTE:
            vig_id = self.vignette.vignette_id if self.vignette_id else self.vignette_ref
            if self.vignette_action:
                return f'[{self.order}] vignette/{self.vignette_action}: {vig_id or self.text[:40]}'
            return f'[{self.order}] vignette: {vig_id}'
        if self.kind == self.DISPATCH:
            return f'[{self.order}] dispatch'
        if self.kind == self.INPUT:
            field_id = self.input_case_field.field_id if self.input_case_field_id else self.input_field
            return f'[{self.order}] input: {field_id}'
        if self.kind == self.TOOL_SEARCH:
            tool_id = self.tool_search.search_id if self.tool_search_id else self.tool_ref
            return f'[{self.order}] tool-search: {tool_id}'
        if self.kind == self.WA:
            return f'[{self.order}] wa: {self.wa_contact or "dani"} ({self.media_type or "text"})'
        if self.kind == self.SFX:
            action = self.sfx_action or 'play'
            if action == 'stop':
                return f'[{self.order}] sfx: stop'
            if action == 'replace':
                return f'[{self.order}] sfx: replace {self.sfx_key or "notif"}'
            return f'[{self.order}] sfx: {self.sfx_key or "notif"}'
        if self.kind == self.AMBIENT:
            if self.ambient_stop:
                return f'[{self.order}] ambient: stop'
            return f'[{self.order}] ambient: {self.ambient_key or "(sin clave)"}'
        return f'[{self.order}] {self.speaker}: {self.text[:60]}'


# ---------------------------------------------------------------------------
# Top-level project
# ---------------------------------------------------------------------------

class GameProject(models.Model):
    name = models.CharField(max_length=200, default='Dispatcher')
    # Renamed from start_scene_id to avoid conflict with the FK column Django auto-creates
    start_scene_str = models.CharField(
        max_length=100, default='intro', blank=True, editable=False,
        help_text='Legacy text ID. Managed automatically via start_scene FK.',
    )
    start_scene = models.ForeignKey(
        'Scene', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Start Scene',
        help_text='First scene to play. Select from dropdown.',
    )

    # Cinema / opening screen
    cinema_image = models.CharField(max_length=500, blank=True)
    cinema_studio = models.CharField(max_length=200, blank=True)
    cinema_title = models.CharField(max_length=200, blank=True)
    cinema_place = models.CharField(max_length=200, blank=True)
    cinema_text = models.TextField(blank=True, help_text='HTML allowed (e.g. <br>)')

    # WhatsApp config
    whatsapp_default_contact = models.CharField(max_length=100, blank=True, editable=False,
        help_text='Legacy text ID. Managed automatically via wa_default_contact FK.')
    wa_default_contact = models.ForeignKey(
        'Contact', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='WhatsApp Default Contact',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def effective_start_scene_id(self):
        """Returns the scene_id string regardless of whether FK or legacy str is used."""
        return self.start_scene.scene_id if self.start_scene_id else self.start_scene_str

    class Meta:
        verbose_name = 'Game Project'

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Assets — attached to GameProject
# ---------------------------------------------------------------------------

class ManualCategory(models.Model):
    project = models.ForeignKey(GameProject, on_delete=models.CASCADE, related_name='manual_categories')
    order = models.PositiveIntegerField(default=0)
    cat_id = models.CharField(max_length=100, help_text='Slug identifier (e.g. "vehiculo_robado")')
    name = models.CharField(max_length=200)
    keys = models.JSONField(default=list, help_text='Keywords that trigger this category')
    when = models.TextField(help_text='When to use this category')
    steps = models.JSONField(default=list, help_text='Steps list (array of strings)')
    avoid = models.JSONField(default=list, help_text='Things to avoid (array of strings)')
    dispatch = models.CharField(max_length=500, help_text='Dispatch recommendation text')

    class Meta:
        verbose_name = 'Manual Category'
        verbose_name_plural = 'Manual Categories'
        ordering = ['order']

    def __str__(self):
        return f'{self.name} ({self.cat_id})'


class Vignette(models.Model):
    project = models.ForeignKey(GameProject, on_delete=models.CASCADE, related_name='vignettes')
    vignette_id = models.CharField(max_length=100, help_text='Key used in JSON (e.g. "office")')
    image = models.CharField(max_length=500, help_text='Relative path to image asset')
    label = models.CharField(max_length=200, blank=True, help_text='Location/label shown on screen')
    subtitle = models.TextField(blank=True, help_text='Subtitle shown below label')

    class Meta:
        verbose_name = 'Vignette'
        ordering = ['vignette_id']

    def __str__(self):
        return f'{self.vignette_id} — {self.label}'


class AudioAsset(models.Model):
    MUSIC = 'music'
    SFX = 'sfx'
    TYPE_CHOICES = [(MUSIC, 'Music'), (SFX, 'SFX')]

    project = models.ForeignKey(GameProject, on_delete=models.CASCADE, related_name='audio_assets')
    asset_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=MUSIC)
    key = models.CharField(max_length=100, help_text='Key used in JSON (e.g. "rain", "notif")')
    path = models.CharField(max_length=500, help_text='Relative path to audio file')

    class Meta:
        verbose_name = 'Audio Asset'
        ordering = ['asset_type', 'key']

    def __str__(self):
        return f'{self.asset_type}/{self.key}: {self.path}'


class Contact(models.Model):
    project = models.ForeignKey(GameProject, on_delete=models.CASCADE, related_name='contacts')
    contact_id = models.CharField(max_length=100, help_text='Key used in JSON (e.g. "dani")')
    name = models.CharField(max_length=200)
    extra_data = models.JSONField(
        default=dict, blank=True,
        help_text='Additional contact fields (avatar, phone, etc.) as JSON',
    )

    class Meta:
        verbose_name = 'Contact'
        ordering = ['contact_id']

    def __str__(self):
        return f'{self.name} ({self.contact_id})'


# ---------------------------------------------------------------------------
# Scenes — the narrative units
# ---------------------------------------------------------------------------

class Scene(models.Model):
    CHANNEL_NARRATION = 'narration'
    CHANNEL_911 = '911'
    CHANNEL_WHATSAPP = 'whatsapp'
    CHANNEL_CHOICES = [
        (CHANNEL_NARRATION, 'Narration'),
        (CHANNEL_911, '911 Call'),
        (CHANNEL_WHATSAPP, 'WhatsApp'),
        ('', 'Other / Not set'),
    ]

    project = models.ForeignKey(GameProject, on_delete=models.CASCADE, related_name='scenes')
    case = models.ForeignKey(
        'Case', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='scenes', verbose_name='Case (optional)',
        help_text='Associate this scene with a case to filter inputs/tools/dispatch.',
    )
    scene_id = models.CharField(max_length=100, help_text='Unique scene identifier (e.g. "intro")')
    title = models.CharField(max_length=200)
    objective = models.TextField(blank=True, help_text='Internal note — scene purpose')
    channel = models.CharField(
        max_length=50, blank=True, choices=CHANNEL_CHOICES,
        help_text='"911", "narration", "whatsapp", etc.',
    )
    # Renamed from next_scene_id to avoid conflict with the FK column Django auto-creates
    next_scene_str = models.CharField(
        max_length=100, blank=True, editable=False,
        help_text='Legacy text ID. Managed automatically via next_scene FK.',
    )
    next_scene = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='previous_scenes', verbose_name='Next Scene',
        help_text='Scene to play after this one. Leave blank if choices control flow.',
    )

    @property
    def effective_next_scene_id(self):
        """Returns the next scene_id string regardless of FK or legacy str."""
        return self.next_scene.scene_id if self.next_scene_id else self.next_scene_str

    class Meta:
        verbose_name = 'Scene'
        ordering = ['scene_id']

    def __str__(self):
        return f'{self.scene_id} — {self.title}'

    def is_emergency(self):
        return self.channel == self.CHANNEL_911


class Beat(AbstractBeat):
    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name='beats')

    class Meta(AbstractBeat.Meta):
        verbose_name = 'Beat'
        ordering = ['order']


class Choice(models.Model):
    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name='choices')
    at_beat = models.PositiveIntegerField(
        help_text='Beat index (0-based) after which this choice appears',
    )
    prompt = models.TextField(help_text='Question shown to the player')

    class Meta:
        verbose_name = 'Choice'
        ordering = ['at_beat']

    def __str__(self):
        return f'Choice @beat {self.at_beat}: {self.prompt[:60]}'


class ChoiceOption(models.Model):
    choice = models.ForeignKey(Choice, on_delete=models.CASCADE, related_name='options')
    order = models.PositiveIntegerField(default=0)
    label = models.CharField(max_length=500, help_text='Button label shown to player')
    goto = models.CharField(
        max_length=100, blank=True, editable=False,
        help_text='Legacy text ID. Managed automatically via goto_scene FK.',
    )
    goto_scene = models.ForeignKey(
        'Scene', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Go To Scene',
        help_text='Scene to jump to after option beats play.',
    )

    @property
    def effective_goto(self):
        """Returns the goto scene_id string regardless of FK or legacy str."""
        return self.goto_scene.scene_id if self.goto_scene_id else self.goto

    class Meta:
        verbose_name = 'Choice Option'
        ordering = ['order']

    def __str__(self):
        return f'[{self.order}] {self.label[:60]}'


class OptionBeat(AbstractBeat):
    option = models.ForeignKey(ChoiceOption, on_delete=models.CASCADE, related_name='beats')

    class Meta(AbstractBeat.Meta):
        verbose_name = 'Option Beat'
        ordering = ['order']


# ---------------------------------------------------------------------------
# Cases — playable dispatcher scenarios
# ---------------------------------------------------------------------------

class Case(models.Model):
    project = models.ForeignKey(GameProject, on_delete=models.CASCADE, related_name='cases')
    case_id = models.CharField(
        max_length=100, help_text='Unique case identifier (e.g. "tutorial_mirna_auto_robado")',
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    # Renamed from start_scene_id to avoid conflict with the FK column Django auto-creates
    start_scene_str = models.CharField(
        max_length=100, blank=True, editable=False,
        help_text='Legacy text ID. Managed automatically via start_scene FK.',
    )
    start_scene = models.ForeignKey(
        'Scene', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Start Scene',
        help_text='Scene where this case begins. Select from dropdown.',
    )

    @property
    def effective_start_scene_id(self):
        """Returns the start scene_id string regardless of FK or legacy str."""
        return self.start_scene.scene_id if self.start_scene_id else self.start_scene_str

    class Meta:
        verbose_name = 'Case'
        ordering = ['case_id']

    def __str__(self):
        return f'{self.title} ({self.case_id})'


class CaseField(models.Model):
    """
    Represents an input field the dispatcher must fill during a case.
    Maps to the case_fields dict in dispatcher_story.json.
    """
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='case_fields')
    field_id = models.CharField(
        max_length=100, help_text='Field key (e.g. "plate", "local")',
    )
    fact_path = models.CharField(
        max_length=200, help_text='Dot-notation path in facts object (e.g. "facts.plate")',
    )
    log_template = models.CharField(
        max_length=500, help_text='Log message template (e.g. "✔ Patente: {value}")',
    )
    notification_label = models.CharField(
        max_length=200, help_text='Notification label shown to player (e.g. "PATENTE REGISTRADA")',
    )

    class Meta:
        verbose_name = 'Case Field'
        ordering = ['field_id']

    def __str__(self):
        return f'{self.field_id} → {self.fact_path}'


class ToolSearch(models.Model):
    """
    A tool search panel available during a case (map lookup, web catalog, etc.).
    Maps to the tool_search dict in dispatcher_story.json.
    """
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name='tool_searches')
    search_id = models.CharField(
        max_length=100, help_text='Tool key (e.g. "address", "vehicle")',
    )
    title = models.CharField(max_length=200, help_text='Panel title shown to player')
    hint = models.TextField(blank=True, help_text='Hint text shown above the search input')
    placeholder = models.CharField(max_length=200, blank=True, help_text='Input placeholder text')
    match_patterns = models.JSONField(
        default=list, blank=True,
        help_text='Strings that trigger a successful match (array of strings)',
    )
    result_data = models.JSONField(
        default=dict, blank=True,
        help_text=(
            'Result payload — use {"result": {...}} for map searches '
            'or {"models": [...]} for catalog searches. '
            'This is merged directly into the exported JSON.'
        ),
    )

    class Meta:
        verbose_name = 'Tool Search'
        verbose_name_plural = 'Tool Searches'
        ordering = ['search_id']

    def __str__(self):
        return f'{self.search_id}: {self.title}'


class DispatchRule(models.Model):
    """
    Dispatch configuration for a case: which units are available, which are required.
    One per case (OneToOne).
    """
    case = models.OneToOneField(Case, on_delete=models.CASCADE, related_name='dispatch_rule')
    case_title = models.CharField(max_length=200, help_text='Title shown on dispatch screen')
    available_units = models.JSONField(
        default=list,
        help_text='Array of unit objects: [{"id": "Carabineros", "icon": "🚔", "desc": "..."}]',
    )
    required_units = models.JSONField(
        default=list,
        help_text='Array of required unit IDs: ["Carabineros", "Seguridad Ciudadana"]',
    )
    # Renamed from next_scene_id to avoid conflict with the FK column Django auto-creates
    next_scene_str = models.CharField(
        max_length=100, blank=True, editable=False,
        help_text='Legacy text ID. Managed automatically via next_scene FK.',
    )
    next_scene = models.ForeignKey(
        'Scene', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Next Scene',
        help_text='Scene to play after dispatch is resolved.',
    )

    @property
    def effective_next_scene_id(self):
        """Returns the next scene_id string regardless of FK or legacy str."""
        return self.next_scene.scene_id if self.next_scene_id else self.next_scene_str

    class Meta:
        verbose_name = 'Dispatch Rule'

    def __str__(self):
        return f'Dispatch: {self.case_title}'


class DispatchOutcome(models.Model):
    """
    One possible outcome after the player dispatches units.
    Beats are stored as JSON since they rarely need per-beat editing.
    """
    rule = models.ForeignKey(DispatchRule, on_delete=models.CASCADE, related_name='outcomes')
    outcome_id = models.CharField(max_length=100, help_text='Outcome key (e.g. "complete")')
    match_type = models.CharField(
        max_length=50, blank=True,
        help_text='"all_required" to match when all required units are dispatched. '
                  'Leave blank and fill match_units for partial matches.',
    )
    match_units = models.JSONField(
        default=list, blank=True,
        help_text='List of unit IDs for partial match (used when match_type is blank)',
    )
    notification = models.CharField(max_length=200, help_text='Notification label shown to player')
    beats_json = models.JSONField(
        default=list,
        help_text='Beats played after this outcome. Same format as scene beats JSON.',
    )

    class Meta:
        verbose_name = 'Dispatch Outcome'
        ordering = ['outcome_id']

    def __str__(self):
        return f'{self.outcome_id} — {self.notification}'
