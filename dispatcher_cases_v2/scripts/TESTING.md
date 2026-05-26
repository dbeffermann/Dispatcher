# Testing — dispatcher_console.html

Guía de arquitectura, convenciones y mantenimiento para la suite de tests del
runtime Dispatcher.

---

## Estructura de archivos

```
scripts/
  run_tests.py              ← CI wrapper: ejecuta todos los suites en orden
  test_input_regression.py  ← Suite 1: regresión de paridad de input (determinista)
  test_e2e_player.py        ← Suite 2: E2E desde la perspectiva del jugador
  validate_story.py         ← Suite 3: validación de esquema y grafo de historia
  TESTING.md                ← este archivo
```

---

## Suite 1 — Regresión de input (`test_input_regression.py`)

### Propósito

Verifica que los tres métodos de avance del jugador (click, Enter, Space) producen
exactamente el mismo efecto en escenas normales y con viñeta activa.  Actúa como
red de seguridad ante cualquier cambio en `triggerPrimaryAction`, `handleBlockedContinue`,
`advance`, `vgnContinue` o `refreshControls`.

### Arquitectura de la suite

```
triggerPrimaryAction(source)   ← ÚNICO punto de entrada (contrato)
        │
        ▼
handleBlockedContinue()        ← router de estado
        │
        ├─ vignetteOpen  → vgnContinue()
        ├─ inputMode     → submitInput()
        └─ (default)     → advance()
```

Los 13 tests comprueban:
- Paridad click / Enter / Space en escenas normales y viñetas
- CSS `pointer-events:none` en el overlay
- HTML estático: ningún botón CONTINUAR llama a `advance()` directamente
- Debounce de 90 ms bloquea doble-click rápido
- `#vgn-continue-btn` se deshabilita durante los 580 ms del fade de cierre

### Helpers de setup y su diseño

#### `enter_scene(page, scene_id)`

```python
page.evaluate("""
    G.gameStarted      = true;
    G.primaryActionAt  = 0;
    G.advanceLastAt    = 0;
    G.advanceInFlight  = false;
    enterScene('{scene_id}');
    G.sceneCutPending  = false;   // ← supresión deliberada del fade
""")
page.wait_for_timeout(200)
```

**Por qué se suprime `G.sceneCutPending`:**

`enterScene()` siempre fija `G.sceneCutPending = true`.  Cuando la primera
llamada a `advance()` detecta esta bandera, llama `doFade(() => advance())` y
retorna sin procesar ningún beat.  `doFade` tarda **360 ms** en disparar el
callback.  Si un test lee `G.beatIndex` antes de que pasen esos 360 ms, ve el
índice original en lugar del esperado — resultado: test no-determinista.

La solución es suprimir la bandera **después** de llamar a `enterScene()` pero
**antes** de que cualquier `advance()` del test se ejecute.  Con
`G.sceneCutPending = false` el primer `advance()` procesa el beat directamente y
los tests son 100% deterministas.

> Esta supresión es intencional y documentada.  Es un artefacto del setup de
> tests, no un bug ni una optimización del motor.

**Por qué se resetean los rate-limiters:**

`G.primaryActionAt` y `G.advanceLastAt` acumulan timestamps entre llamadas.  Sin
reset, el debounce de 90 ms del test anterior puede bloquear la acción del test
siguiente si se ejecutan en rápida sucesión.

#### `open_vignette(page)`

```python
enter_scene(page, "supervisor")
page.evaluate("advance()")      # procesa beat 0: vignette open → beatIndex = 1
page.wait_for_timeout(600)
page.evaluate("G.primaryActionAt = 0; G.advanceLastAt = 0;")
```

**Por qué solo un `advance()`:**

La escena `supervisor` tiene:
- beat 0: `vignette open` → abre overlay, `beatIndex` pasa a 1
- beat 1: `narration` (el siguiente beat cuando el jugador presiona CONTINUAR)
- beat 2: `vignette open` (segundo vignette)

`vgnContinue()` inspecciona el beat en `beatIndex` (beat 1, narración) y decide
que **no** es `vignette seq` → cierra el overlay.

Si se llamara `advance()` dos veces, `beatIndex` llegaría a 2.  Al presionar
CONTINUAR, `vgnContinue()` vería beat 2 (`vignette open`) como "siguiente es
viñeta secuencia" → llamaría `advance()` en lugar de cerrar el overlay → el
overlay permanecería abierto → test FAIL.

### Escenas de test

| Variable                | Escena           | Descripción                               |
|-------------------------|------------------|-------------------------------------------|
| `NORMAL_TEST_SCENE`     | `test_authflow`  | 3 narrations, sin viñetas ni inputs       |
| `VIGNETTE_TEST_SCENE`   | `supervisor`     | Empieza con vignette, luego narración     |

Si se cambia el contenido de estas escenas en `data/dispatcher_story.json`, los
tests pueden necesitar ajuste.  Verificar siempre que `test_authflow` tenga al
menos 1 beat de narración y que `supervisor` beat 0 sea `vignette open` y beat 1
no sea `vignette seq`.

---

## Suite 2 — E2E player (`test_e2e_player.py`)

### Propósito

Probar el runtime como lo haría un jugador real:
- No se manipula ninguna variable `G.*`
- No se llama a ninguna función interna del motor
- Los inputs son exclusivamente click de mouse y teclado
- Las aserciones son sobre el DOM: visibilidad de elementos, clases CSS, conteo de hijos

### Flujo de juego usado

```
click INICIAR
    ↓ (900 ms startGame delay)
escena bienvenida — enterScene llamado, G.sceneCutPending=true
    ↓ player: click CONTINUAR  → doFade(360 ms) → advance() → beat 0: kind=input
beat 0: input visible (#inp-answer)
    ↓ player: type "clavesecreta" + Enter
beat 1: vignette open → overlay visible
    ↓ player: click / Enter / Space  (x4, una por beat de la cadena)
        beat 2: sfx           (audio side-effect → vgnContinue llama advance(), overlay sigue abierto)
        beat 3: vignette open (vignette seq → vgnContinue llama advance(), overlay sigue abierto)
        beat 4: ambient       (audio side-effect → vgnContinue llama advance(), overlay sigue abierto)
        beat 5: line ERIC     (narrative → vgnContinue cierra overlay, 580 ms fade)
overlay cerrado (G.vignetteClosing=true, #btn-continue disabled durante esos 620 ms)
    ↓ player: click / Enter / Space → advance() → beat 5 procesado → nuevo entry en #narr-feed
```

La función `click_until_vignette_closes()` maneja el loop de clicks automáticamente, sin hard-codear el número exacto de beats en la cadena.

### Por qué los tests de narración esperan antes de actuar

Cuando `vgnContinue` cierra el overlay por narrative beat, activa `G.vignetteClosing = true`
y deshabilita `#btn-continue` (`disabled=true`, `onclick=null`) durante el fade de salida.
El motor lo restablece después de **620 ms**.

- `page.click("#btn-continue")` — Playwright auto-espera que el elemento sea **clickable** (no disabled), por lo que funciona sin espera explícita.
- `page.keyboard.press("Enter")` — no tiene auto-espera: si se dispara mientras `disabled=true`, se descarta en silencio.

`_reach_narration_mode()` usa `page.wait_for_selector("#btn-continue:not([disabled])")` para bloquear hasta que el motor reactiva el botón.  Es una espera basada en estado real del DOM, no en tiempo fijo.

Después de `enterScene()` (disparado por `startGame` → 900 ms timer), el motor
fija `G.sceneCutPending = true`.  El primer `advance()` desencadena `doFade()`
que tarda **360 ms**.  Los tests E2E esperan **2200 ms** después de INICIAR para
cubrir:
- 900 ms timer de `startGame`
- 360 ms fade de `enterScene`
- 600 ms buffer (fade-in CSS de la viñeta que aparece después del input beat)

No se necesita (ni se hace) ninguna supresión interna.  El juego completa el fade
por sí solo antes de que el test actúe.

### Observables DOM usados

| Observable                                          | Qué mide                          |
|-----------------------------------------------------|-----------------------------------|
| `page.is_visible("#inp-answer")`                    | Input mode activo                 |
| `#vignette-overlay.classList.contains('vgn-visible')` | Overlay de viñeta activo        |
| `#narr-feed.children.length`                        | Número de beats renderizados      |

---

## Correr los tests

```bash
# Desde dispatcher_cases_v2/

# Suite completa (pre-release gate)
python scripts/run_tests.py

# Suite individual
python scripts/test_input_regression.py
python scripts/test_e2e_player.py

# Con ventana de browser (diagnóstico)
python scripts/test_input_regression.py --headed
python scripts/test_e2e_player.py --headed
```

### Browsers soportados

Los scripts intentan en orden: **Edge** → **Chrome** → **Playwright Chromium**.
Edge está preinstalado en Windows 10/11.  Si ninguno está disponible:

```bash
python -m playwright install chromium
```

---

## Cuándo actualizar los tests

| Cambio en el código                                         | Acción                                              |
|-------------------------------------------------------------|-----------------------------------------------------|
| Renombrar o mover `triggerPrimaryAction`                    | Actualizar `test_buttons_use_trigger_not_advance`   |
| Cambiar el debounce de 90 ms                                | Actualizar `test_debounce_blocks_double_click`       |
| Cambiar el fade de cierre de 580 ms                         | Actualizar `test_vgn_btn_disabled_during_close`; el `wait_for_selector` en `_reach_narration_mode` sigue siendo válido porque espera estado real |
| Cambiar beats de `test_authflow` o `supervisor`             | Revisar `NORMAL_TEST_SCENE` / `VIGNETTE_TEST_SCENE` |
| Cambiar la contraseña de bienvenida (`clavesecreta`)        | Actualizar `PASSWORD` en `test_e2e_player.py`        |
| Cambiar el delay de `startGame` (900 ms)                    | Aumentar el wait en `start_session()` del E2E        |
| Cambiar el delay de `doFade` (360 ms)                       | Sin cambio necesario: el E2E ya espera 2200 ms       |
