"""
Repara inconsistencias entre DB y la historia canónica.

Problemas identificados:
1. dispatch_choice beat id=102: empty DIAL → debe ser kind=dispatch
2. call_fragment: falta beat NARR 'De pronto, el mensaje se corta.' entre order=1 y order=2
3. investigation beat id=116: tiene texto extra '— TEXTO EDITADO DESDE...' al final
4. investigation beats id=125,126 (order=9,10): beats MENSAJE extra → eliminar
   + ajustar choice at_beat de 11 a 9
5. dani_chat beat id=98 (order=6): WA de prueba 'Te adjunto una imagen...' → eliminar
   + ajustar choice at_beat de 6 a 5

Ejecutar desde authoring/ con: python debug_repair_db.py
"""
import os, sys, django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'authoring_project.settings')
sys.path.insert(0, '.')
django.setup()

from dispatcher_authoring.models import Beat, Scene, Choice

DRY_RUN = False   # cambiar a True para previsualizar sin modificar

def log(msg):
    print(msg)

def fix(label, fn):
    log(f"\n[FIX] {label}")
    if DRY_RUN:
        log("  [DRY RUN — skip]")
    else:
        fn()
        log("  OK")


# -------------------------------------------------------------------
# 1. dispatch_choice beat id=102 → kind='dispatch'
# -------------------------------------------------------------------
def fix_dispatch_beat():
    b = Beat.objects.get(id=102)
    log(f"  Before: kind={b.kind!r} speaker={b.speaker!r} text={b.text!r}")
    b.kind = 'dispatch'
    b.speaker = ''
    b.text = ''
    b.save()
    log(f"  After:  kind={b.kind!r}")

fix("dispatch_choice beat#102: empty DIAL → dispatch", fix_dispatch_beat)


# -------------------------------------------------------------------
# 2. call_fragment: insertar NARR entre order=1 y order=2
# -------------------------------------------------------------------
def fix_call_fragment_missing_beat():
    s = Scene.objects.get(scene_id='call_fragment')
    # Shift all beats with order >= 2 up by 1
    beats_to_shift = Beat.objects.filter(scene=s, order__gte=2).order_by('-order')
    for b in beats_to_shift:
        log(f"  Shift order {b.order} → {b.order+1} (id={b.id})")
        b.order = b.order + 1
        b.save()
    # Insert new beat at order=2
    new_beat = Beat(
        scene=s,
        order=2,
        kind='narration',
        text='De pronto, el mensaje se corta.',
    )
    new_beat.save()
    log(f"  Inserted NARR 'De pronto, el mensaje se corta.' at order=2 (id={new_beat.id})")
    # Verify
    total = Beat.objects.filter(scene=s).count()
    log(f"  Total beats now: {total}")

fix("call_fragment: add missing NARR 'De pronto, el mensaje se corta.' at order=2",
    fix_call_fragment_missing_beat)


# -------------------------------------------------------------------
# 3. investigation beat id=116: remove extra text suffix
# -------------------------------------------------------------------
def fix_investigation_text():
    b = Beat.objects.get(id=116)
    original = b.text
    log(f"  Before: {original!r}")
    # Remove everything from ' — ' onwards
    clean = 'Señora, para continuar voy a necesitar que mantenga la calma'
    b.text = clean
    b.save()
    log(f"  After:  {b.text!r}")

fix("investigation beat#116: remove extra suffix from text", fix_investigation_text)


# -------------------------------------------------------------------
# 4. investigation: delete beats id=125,126 + fix choice at_beat
# -------------------------------------------------------------------
def fix_investigation_extra_beats():
    s = Scene.objects.get(scene_id='investigation')

    for bid in [125, 126]:
        b = Beat.objects.get(id=bid)
        log(f"  Delete beat id={bid} order={b.order} speaker={b.speaker!r} text={b.text!r}")
        b.delete()

    # Fix choice at_beat: was 11 (after 11 beats), now 9 (after 9 beats)
    ch = Choice.objects.filter(scene=s).first()
    if ch:
        log(f"  Choice at_beat before: {ch.at_beat}")
        ch.at_beat = 9
        ch.save()
        log(f"  Choice at_beat after:  {ch.at_beat}")

    total = Beat.objects.filter(scene=s).count()
    log(f"  Total beats now: {total}")

fix("investigation: delete 2 extra MENSAJE beats (id=125,126) + fix choice at_beat→9",
    fix_investigation_extra_beats)


# -------------------------------------------------------------------
# 5. dani_chat: delete beat id=98 + fix choice at_beat
# -------------------------------------------------------------------
def fix_dani_chat_extra_beat():
    s = Scene.objects.get(scene_id='dani_chat')

    b = Beat.objects.get(id=98)
    short = b.text[:60]
    log(f"  Delete beat id=98 order={b.order} kind={b.kind!r} text={short!r}")
    b.delete()

    # Fix choice at_beat: was 6 (after 6 beats), now 5 (after 5 beats)
    ch = Choice.objects.filter(scene=s).first()
    if ch:
        log(f"  Choice at_beat before: {ch.at_beat}")
        ch.at_beat = 5
        ch.save()
        log(f"  Choice at_beat after:  {ch.at_beat}")

    total = Beat.objects.filter(scene=s).count()
    log(f"  Total beats now: {total}")

fix("dani_chat: delete extra WA beat id=98 + fix choice at_beat→5",
    fix_dani_chat_extra_beat)


# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------
print("\n" + "="*60)
print("DONE. Run 'python manage.py export_dispatcher_json' to update the JSON.")
print("="*60)
