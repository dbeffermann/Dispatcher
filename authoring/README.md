# Studio de Autoría — Django

Herramienta principal de creación de contenido narrativo para juegos
basados en el runtime Dispatcher. El Studio es la única fuente de verdad
para el contenido del juego; todos los demás artefactos se derivan de él.

---

## Arquitectura: tres capas, una fuente de verdad

```
┌─────────────────────────────────────────────────────────────────┐
│                     STUDIO  (fuente de verdad)                  │
│   Django + SQLite · interfaz visual de nodos · panel de edición │
│                                                                  │
│  Modelos: GameProject, Scene, Beat, Choice, ChoiceOption, ...    │
└──────────────┬──────────────────────────────┬───────────────────┘
               │  export_yaml                  │  export_dispatcher_json
               ▼                               ▼
┌──────────────────────────┐   ┌─────────────────────────────────┐
│  CAPA YAML  (versioning) │   │  game.json  (artefacto runtime) │
│  yaml/scenes/*.yaml      │   │  data/dispatcher_story.json     │
│  yaml/project.yaml       │   │  data/dispatcher_assets.json    │
│                          │   │                                 │
│  • Git / control versión │   │  NUNCA se edita a mano.         │
│  • Edición humana avanzada│  │  Siempre se regenera desde DB.  │
│  • Integración con IA    │   └─────────────────────────────────┘
│  • Backup legible        │
└──────────────┬───────────┘
               │  import_yaml
               └─────────────────────────────▶ Studio DB
```

### Principios de la arquitectura

| Capa | Rol | ¿Se edita a mano? |
|------|-----|-------------------|
| **Studio (DB)** | Fuente de verdad. Toda creación y edición ocurre aquí. | Solo vía UI del Studio |
| **YAML** | Capa de serialización. Sirve para versionar, editar en bulk, revisar con IA, y restaurar. | Sí — pero siempre se re-importa al Studio |
| **game.json** | Artefacto compilado consumido por el runtime. | **Nunca** |

> El Studio no es un wrapper sobre el JSON. El JSON es un artefacto que **siempre se regenera** desde el Studio.

---

## Estructura del proyecto

```
authoring/
  manage.py
  validate_roundtrip.py       ← valida que el roundtrip es lossless
  requirements.txt
  authoring_project/          ← configuración Django
    settings.py
    urls.py
  dispatcher_authoring/
    models.py                 ← modelos genéricos (multi-juego)
    admin.py
    management/commands/
      import_dispatcher_json.py   ← bootstrap inicial desde JSON legado
      export_dispatcher_json.py   ← COMPILAR: DB → game.json
      validate_dispatcher_project.py
      export_yaml.py              ← DB → yaml/scenes/*.yaml
      import_yaml.py              ← yaml/scenes/*.yaml → DB  (con backup)
  db.sqlite3                  ← base de datos local
  db_backups/                 ← backups automáticos (creados por import_yaml)
```

Artefactos generados (en `../dispatcher_cases_v2/`):
```
data/
  dispatcher_story.json       ← generado por export_dispatcher_json
  dispatcher_assets.json      ← generado por export_dispatcher_json
  _snapshots/                 ← snapshots generados por validate_roundtrip
yaml/
  project.yaml                ← generado por export_yaml
  scenes/
    bienvenida.yaml
    intro.yaml
    ...
schemas/
  scene.schema.yaml           ← contrato canónico del formato YAML
```

---

## Setup inicial

```bash
cd authoring
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
```

## Comandos

### Importar JSON existente a la base de datos

```bash
python manage.py import_dispatcher_json
```

Importa `data/dispatcher_story.json` y `data/dispatcher_assets.json` al DB de Django.
**Advertencia:** borra y reimporta todo (idempotente).

### Exportar base de datos a JSON

```bash
python manage.py export_dispatcher_json
```

Escribe los JSON de vuelta a `../dispatcher_cases_v2/data/`.
Después de esto, `dispatcher_console.html` refleja los cambios.

Para previsualizar sin escribir archivos:
```bash
python manage.py export_dispatcher_json --dry-run
```

### Validar integridad del proyecto

```bash
python manage.py validate_dispatcher_project
```

Verifica:
- `scene.next` apunta a una escena existente
- `choice_option.goto` apunta a una escena existente
- `beat(kind=input)` referencia un `CaseField` existente
- `beat(kind=tool-search)` referencia un `ToolSearch` existente
- `beat(kind=vignette)` referencia una `Vignette` existente
- Escenas de emergencia (channel=911) detectadas
- Cada caso tiene `DispatchRule`

### Flujo YAML: versioning y edición avanzada

```bash
# Exportar DB → archivos YAML editables
python manage.py export_yaml

# Revisar / editar yaml/scenes/*.yaml manualmente o con IA
# Luego re-importar (crea backup automático del DB antes de escribir)
python manage.py import_yaml

# Volver a compilar game.json
python manage.py export_dispatcher_json
```

Opciones útiles:
```bash
# Solo una escena
python manage.py export_yaml --scene dispatch_choice
python manage.py import_yaml --scene dispatch_choice

# Previsualizar sin escribir
python manage.py export_yaml --dry-run
python manage.py import_yaml --dry-run
```

### Validar roundtrip lossless

```bash
# Ejecuta export_yaml + import_yaml + export_dispatcher_json y compara JSON antes/después
python validate_roundtrip.py

# Resultado esperado:
# RESULT: PASS — roundtrip is lossless (0 differences)
```

### Restaurar desde backup

Los backups se crean automáticamente en `authoring/db_backups/` cada vez que se ejecuta
`import_yaml` en modo real (no dry-run):

```bash
# Listar backups disponibles
ls db_backups/

# Restaurar (reemplazar el DB actual con el backup)
cp db_backups/20260525_132344_before_import_yaml.sqlite3 db.sqlite3
```

---

## Comandos de referencia

| Comando | Qué hace |
|---------|----------|
| `import_dispatcher_json` | Bootstrap: JSON legado → DB (idempotente) |
| `export_dispatcher_json` | **Compilar**: DB → game.json (artefacto runtime) |
| `export_yaml` | Serializar: DB → yaml/scenes/*.yaml |
| `import_yaml` | Re-importar: yaml → DB (con backup automático) |
| `validate_dispatcher_project` | Verificar integridad de FKs y referencias |
| `python validate_roundtrip.py` | Confirmar que el roundtrip es lossless |

---

## Flujo de trabajo recomendado

### Creación normal (día a día)

```
Studio UI  →  guardar  →  export_dispatcher_json  →  probar juego
```

### Edición en bulk / con IA

```
export_yaml  →  editar YAML  →  import_yaml  →  export_dispatcher_json
```

### Versionado

```
export_yaml  →  git add yaml/  →  git commit  →  historial legible de cambios
```

---

## Studio

```bash
python manage.py runserver 8001
# Abrir: http://127.0.0.1:8001/studio/
```

## Admin (Django Admin clásico)

```bash
python manage.py runserver 8001
# Abrir: http://127.0.0.1:8001/admin/
```

### Modelos disponibles

| Modelo           | Descripción                                      |
|------------------|--------------------------------------------------|
| GameProject      | Proyecto raíz — start scene, cinema, WhatsApp   |
| ManualCategory   | Categorías del manual del dispatcher             |
| Scene            | Escenas narrativas con beats y choices           |
| Beat             | Líneas dentro de una escena (diálogo/narración)  |
| Choice           | Punto de decisión dentro de una escena           |
| ChoiceOption     | Opción de choice con beats propios               |
| Case             | Caso jugable con campos, herramientas, despacho  |
| CaseField        | Campo de input del caso (patente, local, etc.)   |
| ToolSearch       | Herramienta de búsqueda (mapa, catálogo)         |
| DispatchRule     | Regla de despacho del caso                       |
| DispatchOutcome  | Resultado posible del despacho                   |
| Vignette         | Imágenes/viñetas de escena                       |
| AudioAsset       | Rutas de audio (música y SFX)                    |
| Contact          | Contactos de WhatsApp                            |

### Inlines por admin

- **GameProjectAdmin** → ManualCategory, Vignette, AudioAsset, Contact
- **CaseAdmin** → CaseField, ToolSearch, DispatchRule
- **SceneAdmin** → Beat, Choice
- **ChoiceAdmin** → ChoiceOption
- **ChoiceOptionAdmin** → OptionBeat
- **DispatchRuleAdmin** → DispatchOutcome

## Tipos de beat

| kind         | Campos usados                                              |
|--------------|------------------------------------------------------------|
| *(blank)*    | `speaker` + `text` — línea de diálogo                     |
| `narration`  | `text` — narración sin personaje                           |
| `vignette`   | `vignette_ref` — ID de viñeta a mostrar                    |
| `input`      | `input_field` + `input_set_value` + `input_answer` + `input_hint` |
| `tool-search`| `tool_ref` — ID de herramienta de búsqueda del caso        |

## Notas

- El DB es SQLite local — no compartido, no en producción.
- Para múltiples editores se puede migrar a PostgreSQL cambiando `DATABASES` en `settings.py`.
- La clave secreta en `settings.py` es solo para uso local. Cambiarla si se expone el servidor.
