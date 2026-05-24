/**
 * beat_inline.js
 *
 * Shows / hides field groups inside Beat (and OptionBeat) inline forms
 * based on the selected `kind` value.
 *
 * Works with StackedInline where each form is a .inline-related block.
 * Uses Django admin's existing jQuery instance.
 *
 * NOTE: Uses deferred init pattern so the script works correctly even when
 * loaded before django.jQuery is set (Django admin script ordering).
 */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ */
  /* Internal init — receives $ = django.jQuery once it is available     */
  /* ------------------------------------------------------------------ */
  function init($) {

  // Fields visible per kind.
  // Groups not listed are hidden; groups listed are shown.
  var ALWAYS_VISIBLE = ['order', 'kind'];

  var KIND_GROUPS = {
    '':           ['beat_text', 'dialogue', 'wa_media'],
    'narration':  ['beat_text'],
    'vignette':   ['vignette_group', 'beat_text'],   // beat_text = subtitle visible on image
    'input':      ['input_group'],
    'tool-search':['tool_group'],
    'wa':         ['beat_text', 'dialogue', 'wa_group', 'wa_media'],
    'sfx':        ['audio_sfx'],
    'ambient':    ['audio_ambient'],
    'dispatch':   [],
  };

  // CSS class → field names mapping
  var GROUPS = {
    beat_text:      ['text'],           // standalone text/subtitle field
    dialogue:       ['speaker'],        // speaker only (text is in beat_text)
    vignette_group: ['vignette_action', 'vignette', 'vignette_ref'],
    input_group:    ['input_case_field', 'input_field', 'input_set_value', 'input_answer', 'input_hint'],
    tool_group:     ['tool_search', 'tool_ref'],
    wa_group:       ['wa_contact'],
    wa_media:       ['media_type', 'media_url', 'media_caption', 'media_name', 'media_duration'],
    audio_sfx:      ['sfx_key'],
    audio_ambient:  ['ambient_key', 'ambient_stop'],
  };

  // Fieldset CSS class → the kind values that should expand (show) that fieldset
  var FIELDSET_KINDS = {
    'beat-group-text':     ['', 'narration', 'vignette', 'wa'], // text/subtitle for all narrative kinds
    'beat-group-dialogue': ['', 'wa'],                          // speaker only for dialogue/wa
    'beat-group-vignette': ['vignette'],
    'beat-group-input':    ['input'],
    'beat-group-tool':     ['tool-search'],
    'beat-group-wa':       ['wa'],
    'beat-group-audio':    ['sfx', 'ambient'],
  };

  // All managed fields (flattened unique list)
  var ALL_FIELDS = [];
  Object.values(GROUPS).forEach(function (fields) {
    fields.forEach(function (f) {
      if (ALL_FIELDS.indexOf(f) === -1) ALL_FIELDS.push(f);
    });
  });

  function getVisibleFields(kind) {
    var groups = KIND_GROUPS[kind] !== undefined ? KIND_GROUPS[kind] : Object.keys(GROUPS);
    var visible = [];
    groups.forEach(function (g) {
      if (GROUPS[g]) {
        GROUPS[g].forEach(function (f) {
          if (visible.indexOf(f) === -1) visible.push(f);
        });
      }
    });
    return visible;
  }

  /**
   * Find the nearest .form-row (or .fieldBox) wrapping a field
   * within a given stacked inline form block $form.
   */
  function findFieldRow($form, fieldName) {
    // Django stacked inlines use .field-{name} class on the .form-row
    return $form.find('.field-' + fieldName);
  }

  function updateForm($form, kind) {
    var visible = getVisibleFields(kind);
    ALL_FIELDS.forEach(function (field) {
      var $row = findFieldRow($form, field);
      if ($row.length) {
        if (visible.indexOf(field) >= 0) {
          $row.show();
        } else {
          $row.hide();
        }
      }
    });

    // Show/hide fieldset containers entirely based on kind.
    // Non-relevant fieldsets are hidden completely (not just collapsed) so
    // authors never see an empty expandable section.
    Object.keys(FIELDSET_KINDS).forEach(function (cls) {
      var $fs = $form.find('fieldset.' + cls);
      if (!$fs.length) return;
      var shouldExpand = FIELDSET_KINDS[cls].indexOf(kind) >= 0;
      if (shouldExpand) {
        $fs.show().removeClass('collapsed');
        $fs.find('.collapse-toggle').text(gettext ? gettext('Hide') : 'Ocultar');
      } else {
        $fs.hide();
      }
    });
  }

  function initForm($form) {
    var $kindSelect = $form.find('select[name$="-kind"], select[id$="-kind"]');
    if (!$kindSelect.length) return;

    var kind = $kindSelect.val() || '';
    updateForm($form, kind);
    if (kind === 'vignette') _initVignetteUX($form);

    $kindSelect.on('change.beatinline', function () {
      var k = $(this).val() || '';
      updateForm($form, k);
      if (k === 'vignette') _initVignetteUX($form);
    });
  }

  // ---------------------------------------------------------------------------
  // Vignette UX helpers
  // ---------------------------------------------------------------------------

  /**
   * Vignette action hints (shown next to the action dropdown).
   * Also manages show/hide of `vignette_ref` depending on whether FK is set,
   * and show/hide of `text` depending on whether the action uses text.
   */
  var ACTION_INFO = {
    '':       { needsVig: true,  needsText: false, label: 'Legacy (abre y cierra automáticamente)' },
    'open':   { needsVig: true,  needsText: true,  label: 'Abre el plano — imagen persiste en pantalla' },
    'text':   { needsVig: false, needsText: true,  label: 'Actualiza subtítulo — imagen permanece' },
    'change': { needsVig: true,  needsText: true,  label: 'Cambia el plano — nueva imagen (+ subtítulo opcional)' },
    'close':  { needsVig: false, needsText: false, label: 'Cierra el overlay — vuelve al juego' },
  };

  function _initVignetteUX($form) {
    // ---- vignette FK select (Select2 autocomplete) ----
    var $vigSel = $form.find('select[name$="-vignette"]');
    var $vigRefRow = $form.find('.field-vignette_ref');
    var $actionSel = $form.find('select[name$="-vignette_action"]');
    var $textRow  = $form.find('.field-text');

    // Hint element below the action dropdown
    var hintId = 'vig-hint-' + ($form.attr('id') || Math.random().toString(36).slice(2));
    var $hint = $form.find('#' + hintId);
    if (!$hint.length) {
      $hint = $('<p id="' + hintId + '" style="margin:3px 0 0 180px;font-size:11px;color:#6b7280;font-style:italic;"></p>');
      $actionSel.closest('.field-vignette_action').append($hint);
    }

    function _updateVigUX() {
      var action = $actionSel.val() || '';
      var info = ACTION_INFO[action] || ACTION_INFO[''];
      var hasFK = !!($vigSel.val());

      // Hint text
      $hint.text(info.label);

      // vignette_ref: only show when no FK is set (legacy fallback)
      if (hasFK) {
        $vigRefRow.hide();
      } else {
        $vigRefRow.show();
      }

      // Highlight vignette row when required but empty
      var $vigRow = $form.find('.field-vignette');
      if (info.needsVig && !hasFK) {
        $vigRow.find('label').css('color', '#dc2626');
      } else {
        $vigRow.find('label').css('color', '');
      }

      // text (subtitle) field: show only when action uses it.
      // Also toggle the entire beat-group-text fieldset so the section
      // header doesn't appear empty when text is irrelevant (e.g. close).
      var $textFieldset = $textRow.closest('fieldset.beat-group-text');
      if (info.needsText) {
        $textFieldset.show().removeClass('collapsed');
        $textRow.show();
      } else {
        $textRow.hide();
        $textFieldset.hide();
      }
    }

    $actionSel.off('change.viguxaction').on('change.viguxaction', _updateVigUX);

    // Select2 fires 'select2:select' and 'select2:clear'; also native 'change'
    $vigSel.off('change.viguxfk').on('change.viguxfk', _updateVigUX);

    _updateVigUX();
  }

  function initAllForms($scope) {
    // Stacked inlines: each beat form is wrapped in a .inline-related div
    $scope.find('.inline-related').each(function () {
      initForm($(this));
    });
  }

  // Register formset:added first (works for dynamically added rows added before/after load).
  // Django 4.x dispatches a CustomEvent on the row element (not a jQuery trigger),
  // so the row is event.target, not the second argument.
  $(document).on('formset:added', function (event, $jqRow) {
    var row = $jqRow || $(event.target);
    if (row && row.length) initForm(row);
  });

  // Apply to existing forms AFTER window.load so Django's collapse.js has already run
  // (collapse.js fires on 'load' and adds the `collapsed` class; we need to run after it).
  if (document.readyState === 'complete') {
    initAllForms($(document));
  } else {
    window.addEventListener('load', function () {
      initAllForms($(document));
    });
  }

  } // end init($)

  /* ------------------------------------------------------------------ */
  /* Deferred runner — waits until django.jQuery is available            */
  /* ------------------------------------------------------------------ */
  function tryInit() {
    var jq = window.django && window.django.jQuery;
    if (typeof jq === 'function') {
      init(jq);
    } else {
      // django.jQuery not yet available — retry at DOMContentLoaded
      document.addEventListener('DOMContentLoaded', tryInit);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tryInit);
  } else {
    tryInit();
  }

}());
