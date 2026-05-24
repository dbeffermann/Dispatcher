# Dispatcher Authoring — Django Admin

Backend de autoría para editar el contenido del juego y exportar los JSON que consume `dispatcher_console.html`.

## Estructura

```
authoring/
  manage.py
  requirements.txt
  authoring_project/       ← configuración Django
    settings.py
    urls.py
  dispatcher_authoring/    ← app principal
    models.py              ← todos los modelos
    admin.py               ← Django Admin con inlines
    management/commands/
      import_dispatcher_json.py
      export_dispatcher_json.py
      validate_dispatcher_project.py
  db.sqlite3               ← base de datos local (generada)
```

Los archivos de juego NO se tocan:
```
../dispatcher_cases_v2/
  dispatcher_console.html   ← runtime del juego (sin cambios)
  data/
    dispatcher_story.json   ← generado por export_dispatcher_json
    dispatcher_assets.json  ← generado por export_dispatcher_json
```

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

## Flujo de trabajo

```
Abrir admin  →  editar contenido  →  export_dispatcher_json  →  abrir dispatcher_console.html
```

## Admin

```bash
python manage.py runserver
# Abrir: http://127.0.0.1:8000/admin/
# Usuario: admin / contraseña definida en createsuperuser
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
