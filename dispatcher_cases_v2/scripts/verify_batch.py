"""Verifica el batch de escenas modificadas en el JSON compilado."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
story = json.loads((ROOT / "data" / "dispatcher_story.json").read_text(encoding="utf-8"))

scenes = {s["id"]: s for s in story["scenes"]}
cases  = {c["id"]: c for c in story.get("cases", [])}

CASE_ID = "tutorial_mirna_auto_robado"
case = cases.get(CASE_ID, {})

# ── 1. Case fields ──────────────────────────────────────────────────────────
print("=== case_fields ===")
cf_ids = [f["field_id"] for f in case.get("case_fields", [])]
for f in cf_ids:
    print(f"  {f}")
REQUIRED_FIELDS = {"local", "plate", "vehicle"}
missing = REQUIRED_FIELDS - set(cf_ids)
if missing:
    print(f"  [FAIL] missing fields: {missing}")
else:
    print(f"  [OK] local, plate, vehicle present")

# ── 2. investigation ────────────────────────────────────────────────────────
inv = scenes["investigation"]
print()
print("=== investigation ===")
print(f"  case_id : {inv.get('case_id')}")
print(f"  next    : {inv.get('next')}")
print()
for i, b in enumerate(inv["beats"]):
    kind   = b.get("kind", "dialogue")
    field  = b.get("field", "")
    tool   = b.get("tool", "")
    spk    = b.get("speaker", "")
    txt    = b.get("text", "")[:45]
    print(f"  [{i:02d}] {kind:12s}  field={field:<10} tool={tool:<5}  {spk} {txt}")

# choice.at alignment
print()
for ch in inv.get("choices", []):
    at = ch["at"]
    beat_at = inv["beats"][at - 1] if at <= len(inv["beats"]) else None
    print(f"  choice at:{at}  -> beat[{at-1}] kind={beat_at.get('kind','dialogue') if beat_at else 'N/A'}")
    for opt in ch["options"]:
        print(f"    '{opt['label']}' goto={opt.get('goto','(continue)')}")

# ── 3. dispatch_choice ──────────────────────────────────────────────────────
dc = scenes["dispatch_choice"]
print()
print("=== dispatch_choice ===")
print(f"  case_id : {dc.get('case_id')}")
for i, b in enumerate(dc["beats"]):
    kind = b.get("kind", "dialogue")
    spk  = b.get("speaker", "")
    txt  = b.get("text", "")[:50]
    print(f"  [{i}] {kind:12s}  {spk} {txt}")

# ── 4. call_fragment choice ─────────────────────────────────────────────────
cf = scenes["call_fragment"]
print()
print("=== call_fragment ===")
print(f"  case_id : {cf.get('case_id')}")
for ch in cf.get("choices", []):
    print(f"  choice at:{ch['at']}")
    for opt in ch["options"]:
        ob_count = len(opt.get("beats", []))
        print(f"    '{opt['label']}' goto={opt.get('goto','(continue)')} option_beats={ob_count}")

# ── 5. manual_search choices ────────────────────────────────────────────────
ms = scenes["manual_search"]
print()
print("=== manual_search ===")
print(f"  case_id : {ms.get('case_id')}")
for ch in ms.get("choices", []):
    print(f"  choice at:{ch['at']}")
    for opt in ch["options"]:
        ob = len(opt.get("beats", []))
        print(f"    '{opt['label']}' goto={opt.get('goto','(continue)')} ob={ob}")

# ── 6. dani_chat ────────────────────────────────────────────────────────────
dc2 = scenes["dani_chat"]
print()
print("=== dani_chat ===")
for ch in dc2.get("choices", []):
    print(f"  choice at:{ch['at']}")
    for opt in ch["options"]:
        ob = len(opt.get("beats", []))
        print(f"    '{opt['label']}' goto={opt.get('goto','(continue)')} ob={ob}")

print()
print("=== OVERALL ===========================================")
ok = not missing and inv.get("case_id") == CASE_ID and dc.get("case_id") == CASE_ID
print("PASS" if ok else "FAIL")
