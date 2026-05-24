# Dispatcher — V2 data-driven con múltiples casos

Esta versión separa el runtime del contenido editable y agrega soporte para `cases[]`.

## Archivos principales

- `dispatcher_console.html`: runtime/UI del juego. No debería tocarse para editar historia.
- `data/dispatcher_story.json`: guion, escenas, casos, inputs, búsquedas y despacho.
- `data/dispatcher_assets.json`: audio, viñetas, cinemática inicial y contactos WhatsApp.
- `tools/json_editor.html`: editor JSON liviano con botones para agregar casos, escenas, diálogos, inputs, búsquedas, decisiones y viñetas.
- `scripts/validate_story.py`: validador local.

## Cómo ejecutar

```bash
cd dispatcher_cases_v2
python -m http.server 8080
```

Luego abrir:

```text
http://localhost:8080/dispatcher_console.html
```

Editor:

```text
http://localhost:8080/tools/json_editor.html
```

## Modelo nuevo

Antes existía solo un caso activo global:

```json
{
  "scenes": [],
  "case_fields": {},
  "tool_search": {},
  "dispatch_rules": {}
}
```

Ahora existe:

```json
{
  "scenes": [],
  "cases": [
    {
      "id": "tutorial_mirna_auto_robado",
      "title": "Tutorial — Vehículo robado",
      "start_scene": "mirna_open",
      "case_fields": {},
      "tool_search": {},
      "dispatch_rules": {},
      "initial_facts": {}
    }
  ]
}
```

Cada escena puede tener:

```json
"case_id": "tutorial_mirna_auto_robado"
```

Escenas sin `case_id` son globales: intro, supervisor, Dani, transiciones.

## Caso actual

El caso de Mirna quedó como:

```text
Case 01: Tutorial — Vehículo robado
```

Contiene sus propios:

- `case_fields`
- `tool_search`
- `dispatch_rules`
- `initial_facts`

Así futuros casos no mezclan patente, mapa, despacho ni datos con el tutorial.

## Validar

```bash
python scripts/validate_story.py
```

Debe terminar con:

```text
[OK] validación sin errores
```

## Agregar un caso nuevo

1. Abre `tools/json_editor.html`.
2. Clic en `+ Caso`.
3. Clic en `+ Escena` y asigna ese `case_id`.
4. Agrega diálogos, inputs, búsquedas y despacho.
5. Conecta la escena anterior editando `next`.
6. Valida.
7. Descarga y reemplaza `data/dispatcher_story.json`.

## Compatibilidad

El runtime todavía soporta `case_fields`, `tool_search` y `dispatch_rules` globales como fallback legacy, pero el camino recomendado es usar `cases[]`.
