# Panel audit, group 1: measured accessibility and usability defects — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Execution (user, 2026-10-08):** subagent-driven (superpowers:subagent-driven-development), in a separate git worktree (superpowers:using-git-worktrees), starting only after the Rust round has finished its engine cost measurements on this machine.

**Goal:** Fix the seven measured, cheap defects of the panel audit's group 1 (header volume name, Parlantes losing focus, `(?)` help popovers, contrast, live regions, the "Deshacer" notice, and four details), plus the three extensions the user added on 2026-10-08 (per-speaker sliders' value text, focus lost on "Cargar" a preset, the other light-mode graphics under 3:1), each guarded by a browser test that was seen to fail first, with axe-core added as the per-view regression guard.

**Architecture:** One new test helper module (`host/tests_browser/a11y.py`: axe injection, in-page colour/contrast maths ported from `probes/23-auditoria-panel/audit.js`, mutation counting) and one new test file for the audit (`test_panel_accessibility.py`); the axe test is a ratchet whose list of known violations only shrinks. The fixes live where each control is built: `panel/index.html`, `panel/app.js` and `panel/tailwind.input.css` (hand-written, no build step except `hatch run web:css`), and `host/web/src/undo.ts` (built into `panel/cadena.js` by `npm run build`, stamped).

**Tech Stack:** vanilla JS panel (`host/src/aurasync/panel/app.js`), Tailwind 4.3.3 via `hatch run web:css`; Preact/TS in `host/web/` (Vite, vitest); Playwright 1.60.0 + pytest via `hatch run browser:test` (Chromium and Firefox); axe-core 4.14.0 (npm, dev only); Python 3.12 for the one service-side fix.

**Spec:** there is no design doc. The spec is `docs/research/10-panel-de-control.md` §10 "Grupo 1" (items 1–7, the proposed fixes) plus `docs/research/experimentos/22-auditoria-medida-del-panel.md` (the measured evidence: selectors, values and how each was measured; raw data in `docs/research/experimentos/datos/22-auditoria-panel/`, measurement scripts in `probes/23-auditoria-panel/`). Binding decision: d-7c8794-a5f3ba (axe-core). Scope extensions and answers: the user, 2026-10-08 (see "Open questions — answered" at the end). Out of scope: d-7c8794-b7cdbd (the PC lateral layout) and groups 2–3 of §10.

## Global Constraints

- **Thresholds (WCAG 2.2 AA, as the audit measured them):** text ≥ **4.5:1** (all panel text is < 18 pt); graphics, field borders, meter zones and tracks, focus rings ≥ **3:1**; a focused control is still focused **3 s** later; a live region is written **0 times in 5 s** while its state does not change; the undo notice lasts `UNDO_MS = 10_000` of *unheld* time; cumulative layout shift on load ≤ **0.1** (Web Vitals "good"; Lighthouse measured 0.513 desktop, 0.788 mobile).
- **axe-core 4.14.0, exact** (`npm view axe-core` on 2026-10-08: 4.14.0, MPL-2.0), in `devDependencies` of `host/web/package.json`, used only by `host/tests_browser/`; never imported from `host/web/src`, never served from `/static/` (d-7c8794-a5f3ba). **Reading of d-7c8794-a5f3ba (user, 2026-10-08):** the full axe matrix runs in the browser tests (`hatch run browser:test`); `scripts/check.sh` carries only the cheap checks (exact pinned version, dev-only, not shipped) through `hatch test` (Task 1's `test_web_dev_dependencies.py`). Tags: `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa` (`wcag22aa` makes `target-size` run). Matrix: each of the 6 views × {light, dark, forced-colors} × {1366×900, 390×844}.
- **Copy:** everything the panel shows or announces (text, `aria-label`, `aria-valuetext`) is Spanish, with Spanish number formatting (`nf()` in `app.js`: decimal comma). New code, identifiers, docstrings and test names in English (d-7c8794-7b3093); comments in `app.js`, `index.html` and `tailwind.input.css` stay in Spanish (the file's language).
- **Builds:** after editing `panel/tailwind.input.css`, or any class in `host/web/src`: `cd host && hatch run web:css` (writes the versioned `panel/tailwind.css`). After changing anything under `host/web/` (sources, `package.json`, `package-lock.json`): `cd host/web && npm run check && npm test && npm run build`, then `python3 host/scripts/web_stamp.py` must print nothing and exit 0 (the stamp hashes `package.json` and `package-lock.json` too, so adding axe-core alone needs a rebuild).
- **Test commands:** during a task, one browser and one test: `cd host && hatch run browser:test tests_browser/<file>.py -k "<test> and chromium" -x`. Both browsers only at the close (Task 13). `node_modules` must exist: `cd host/web && npm ci`.
- **Machine load:** the browser suite is heavy. Do not run it while another session is measuring engine cost on the same machine; ask first.
- **Simulated only:** every browser test uses `Running` (`Service(simulated=True)`); nothing touches Bluetooth, PipeWire, the WH-CH520 headphones or systemd on `HP-O16`.
- **Do not touch:** `engine/`, `host/src/aurasync/dsp/`, the layouts other than `pestanas`, the Diagnóstico zones (left/right/bottom, asked by the user on 2026-10-01: `app.js:84`), `probes/23-auditoria-panel/` (deleted only when the whole audit is applied, d-7c8794-3208b7).
- **Scope:** fix only what group 1 names plus the three extensions the user approved on 2026-10-08; any further widening found on the way is reported to the controller, not put into the code (user rule: a light observation is not an order to purge).
- **Each new check is seen red once** on the current code (or on a planted violation) before its fix, and that run is noted for the changelog (knowledge card `a-check-must-be-seen-to-fail`). The axe list of known violations lives inside the test and only shrinks (card `ratchet-in-a-pinned-environment`).
- **No commit step:** the repository commits only when the user asks. Each task ends with its tests green, `cd host && hatch fmt --check` clean, and (if it touched `host/web`) the stamp check passing.

## Review Focus

1. **A keyboard user activates "Desconectar"** and the row's actions change → focus stays inside that device row (on its new first button, or on the row's name), never on `body`. Task 3 `test_disconnecting_with_the_keyboard_keeps_focus_in_the_row`.
2. **The notice is held by pointer and focus at once, or replaced while held** → it expires only after every hold is released, and a new offer commits the held one and starts with full time. Task 8 vitest `holds stack` and `a new offer while held commits the old one`.
3. **A help dismissed with Escape is not dismissed forever, and Escape with nothing open is not swallowed** → the next hover or focus opens it again; with no help open, Escape reaches other handlers (`defaultPrevented` false). Task 4 `test_a_dismissed_help_opens_again_and_a_free_escape_passes_through`.
4. **A live region whose text really changes still changes** (monitor off → binaural) → exactly the new text is written, once. Task 7 `test_a_live_region_still_changes_when_its_text_does`.
5. **Forced colours (Windows high contrast)** → the level meter still shows where the level is (filled and empty parts differ by ≥ 3:1 in pixels). Task 6 `test_the_level_meter_survives_forced_colors`.

---

## Decisions taken while planning (each reversible in minutes; say so if the user objects)

- The notice keeps its position (bottom centre); 2.4.11 is met with `scroll-padding-bottom` and extra `#views` bottom padding while it is visible (CSS `:has()`), so focus never lands under it and the page can scroll anything clear of it.
- When the notice appears and focus has fallen to `body`, focus moves to "Deshacer"; when it closes with focus inside it, focus goes to a caller-given fallback (`#preset-name` for a deleted preset, `#scan` for a forgotten device).
- The per-speaker delay field becomes `type="text" inputmode="decimal"` showing `nf(v, 2)` ("4,50"): a `type="number"` field's separator follows the browser locale and cannot be read or tested from the DOM.
- The level meter's colours move into CSS custom properties on `.meter-track`, so tests read them instead of parsing a gradient.
- Character slider `aria-valuetext`: `"0,50: equilibrio"`, `"0,25: más ubicación"`, `"0,75: más envolvimiento"` (below 0.45 / above 0.55).

---

### Task 1: axe-core as the per-view guard

**Files:**
- Modify: `host/web/package.json` (devDependencies), `host/web/package-lock.json` (by npm)
- Modify (rebuilt, not by hand): `host/src/aurasync/panel/cadena.build.json`
- Create: `host/tests_browser/a11y.py`
- Create: `host/tests_browser/test_panel_accessibility.py`
- Create: `host/tests/test_web_dev_dependencies.py`
- Modify: `host/README.md:150-151` (browser tests need `npm ci` in `host/web` once)

**Interfaces:**
- Consumes: `Running`, `TOKEN`, `go`, `start` from `tests_browser/test_panel.py`; `open_panel(browser, svc, width, height)` pattern from `test_panel_usability.py:310` (extended here).
- Produces (in `a11y.py`, used by every later task):
  - `AXE_PATH: Path` = `host/web/node_modules/axe-core/axe.min.js`; `AXE_TAGS: tuple[str, ...]`
  - `VIEWS: tuple[tuple[str, str], ...]` = `(("escuchar", "Escuchar"), ("cadena", "Cadena"), ("parlantes", "Parlantes"), ("calibrar", "Calibrar"), ("diagnostico", "Diagnóstico"), ("ajustes", "Ajustes"))`
  - `SIZES = {"pc": (1366, 900), "phone": (390, 844)}`
  - `open_themed(browser: Browser, svc: Running, *, size: str = "pc", scheme: str = "light") -> Page` — `scheme` ∈ `light | dark | forced` (`forced` = `forced_colors="active"` on a light context); phone gets `is_mobile`/`has_touch` on Chromium; waits for `body[data-ready='1']`.
  - `busy(page: Page) -> None` — the audit's state: "Agregar parlante virtual" (stopped), then `start(page)`.
  - `show(page: Page, view: str) -> None` — clicks the visible tab of `view` (id from `VIEWS`) and waits until `[data-view=<id>]` is visible.
  - `axe_violations(page: Page) -> dict[str, list[str]]` — injects `AXE_PATH` once (`page.add_script_tag(path=…)`), runs `axe.run(document, {runOnly: {type: "tag", values: AXE_TAGS}})`, returns rule id → node targets.
  - `install_color(page: Page) -> None` — defines `window.__a11y = {rgba, over, ratio, bgOf}` (port of `audit.js` lines 8–37: any CSS colour, `oklch` included, to sRGB through a 1×1 canvas; background composited from the ancestors).
  - `ratio(page: Page, a: str, b: str) -> float` — contrast of two CSS colours.
  - `contrast(locator: Locator, prop: str = "color") -> float` — the element's computed `prop` against its effective background (`bgOf`).
  - `rewrites(page: Page, selectors: list[str], ms: int) -> dict[str, int]` — `MutationObserver` (`childList`, `subtree`, `characterData`) on each selector for `ms`; mutation records per selector.

- [ ] **Step 1: Add the dependency and write the failing tests**

`cd host/web && npm install --save-dev --save-exact axe-core@4.14.0 && npm run build`, then `python3 host/scripts/web_stamp.py`.

```python
# host/tests/test_web_dev_dependencies.py
def test_axe_core_is_a_pinned_dev_dependency_that_does_not_ship():
    pkg = json.loads((WEB / "package.json").read_text())
    assert pkg["devDependencies"]["axe-core"] == "4.14.0"          # exact, no ^ or ~
    assert "axe-core" not in pkg.get("dependencies", {})
    for name in ("cadena.js", "app.js", "index.html"):
        assert "axe" not in (PANEL / name).read_text().lower()      # never shipped nor loaded
```

```python
# host/tests_browser/test_panel_accessibility.py
KNOWN: dict[tuple[str, str, str], set[str]] = {...}   # (size, scheme, view) -> rule ids; only shrinks

@pytest.mark.parametrize("scheme", ["light", "dark", "forced"])
@pytest.mark.parametrize("size", ["pc", "phone"])
def test_axe_finds_only_the_known_violations(browser, svc, size, scheme):
    page = open_themed(browser, svc, size=size, scheme=scheme); busy(page)
    found = {}
    for view, _ in VIEWS:
        show(page, view)
        found[view] = set(axe_violations(page))
    expected = {view: KNOWN.get((size, scheme, view), set()) for view, _ in VIEWS}
    assert found == expected   # message: new rules (with targets) and listed-but-gone rules ("remove from KNOWN")

def test_axe_catches_a_planted_violation(page):
    page.evaluate("document.getElementById('source-kind').removeAttribute('aria-label')")
    assert "select-name" in axe_violations(page)
```

Seed `KNOWN` from experimentos/22 §1.1 (measured at 1440; the first run at 1366 confirms or corrects, and any correction is noted): pc-light `calibrar: {color-contrast}`, `diagnostico: {scrollable-region-focusable}`; pc-dark every view `{color-contrast}` plus `diagnostico: {…, scrollable-region-focusable}`; phone-light `calibrar: {color-contrast, scrollable-region-focusable}`, `diagnostico: {scrollable-region-focusable}`; phone-dark every view `{color-contrast}`, plus `scrollable-region-focusable` in `calibrar` and `diagnostico`. `forced` was never measured: its entries are whatever the first run reports, each with a comment "first seen 2026-10-08, not in group 1".

If Firefox rejects or ignores `forced_colors` (check `matchMedia('(forced-colors: active)').matches` in the page; ASSUMPTION that it is Chromium-only), skip `forced` on Firefox with that reason. If Firefox reports a different set for a screen, record it in a separate `KNOWN_FIREFOX` override, not by widening `KNOWN`.

- [ ] **Step 2: Run to see them fail**

Run: `cd host && hatch test tests/test_web_dev_dependencies.py` before the `npm install` → FAIL (`KeyError: 'axe-core'`). Run the browser tests once with an empty `KNOWN` → FAIL listing every current violation (that output is the seed check). Planted test: PASS only if axe runs at all; run it once with `AXE_TAGS = ()` to see it fail.

- [ ] **Step 3: Implement `a11y.py` and fill `KNOWN`** as above. One page per (size, scheme), views visited in order; axe is injected once per page.

- [ ] **Step 4: Run to see them pass**

Run: `cd host && hatch test tests/test_web_dev_dependencies.py && hatch run browser:test tests_browser/test_panel_accessibility.py -k "axe and chromium"` → PASS. `python3 host/scripts/web_stamp.py` → exit 0.

- [ ] **Step 5: No commit** (the repository commits only when the user asks).

---

### Task 2: The header volume and the per-speaker sliders have a name and say their value (group 1, item 1, plus extension (a) of 2026-10-08)

**Files:**
- Modify: `host/src/aurasync/panel/index.html:30-33` (volume), `:158-160` (monitor level), `:290-292` (character)
- Modify: `host/src/aurasync/panel/app.js:604` (`renderControls`), `:2476` (volume `liveRange` callback), `:2999` and `:3029-3031` (monitor), `:2858` and `:2932` (character)
- Modify (extension a, per-speaker sliders): `app.js:382-394` (`liveRange` also writes `aria-valuetext`), `:739-760` (`rangeCell`: a `spoken` formatter, `aria-hidden` on its `<output>`, a `show(v)` method), `:770-775` (Parlantes en detalle: pan, ambience, volume), `:947-951` (quick card: ambience, volume); the sync sites `:749` (↺ reset), `:916` (`renderSpeakers`), `:997` and `:999` (`renderQuick`) call `cell.show(v)` instead of writing `out` directly
- Test: `host/tests_browser/test_panel_accessibility.py`

**Interfaces:**
- Consumes: `open_themed`, `show` (Task 1).
- Produces: `setValueText(input: HTMLInputElement, text: string): void` in `app.js` (writes `aria-valuetext` only when it differs); `characterText(v: number): string`; `liveRange(node, output, format, sendValue, spoken = format)`; `rangeCell(min, max, step, label, format, onValue, neutral = null, spoken = format)` returning `{ input, out, reset, cell, syncReset, show(v: number): void }`. Spoken text: pan `"centro"` / `"izquierda 0,70"` / `"derecha 0,70"` (the visible `C` / `I 0,70` / `D 0,70` is cryptic when heard); volume `"-3,0 dB"` (same as visible); ambience `"0,15"` (no unit, same as visible).

- [ ] **Step 1: Write the failing tests**

```python
def test_the_header_volume_is_a_named_slider_with_its_unit(page):
    slider = page.get_by_role("slider", name="Volumen", exact=True)   # name is the label only (2.5.3)
    expect(slider).to_have_count(1)
    expect(slider).to_have_attribute("aria-valuetext", page.locator("#volume-out").inner_text())  # e.g. "-20 dB"
    slider.focus(); page.keyboard.press("ArrowRight")
    expect(slider).to_have_attribute("aria-valuetext", re.compile(r"^-?\d+ dB$"))
    assert "status" not in page.locator("#player").aria_snapshot()   # the <output> no longer speaks on its own

def test_monitor_and_character_sliders_say_their_value(page):
    expect(page.get_by_role("slider", name="Nivel del monitor")).to_have_attribute("aria-valuetext", re.compile(r"^\d+ (%|dB)$"))
    show(page, "parlantes")
    expect(page.get_by_role("slider", name="Carácter")).to_have_attribute("aria-valuetext", "0,50: equilibrio")

@pytest.mark.parametrize("card", ["speakers", "quick"])        # Parlantes en detalle, and the quick card in Escuchar
def test_per_speaker_sliders_have_a_name_and_say_their_value(page, card):
    # every input[type=range] in the card: a non-empty accessible name ("Pan de …", "Ambiente de …", "Volumen de …")
    # and an aria-valuetext matching its kind; after ArrowRight the valuetext follows the new value
    PAN = r"^(centro|izquierda \d,\d\d|derecha \d,\d\d)$"; GAIN = r"^-?\d+,\d dB$"; AMBIENCE = r"^\d,\d\d$"
    # measured: read as "-20" without unit (experimentos/22 §4.2)

def test_every_label_names_the_control_it_wraps(page):
    # probe2.py `label_mismatch`: label.control must be the input/select/textarea inside the label
    assert page.evaluate(LABEL_MISMATCH) == []
```

- [ ] **Step 2: Run to see them fail**

Run: `cd host && hatch run browser:test tests_browser/test_panel_accessibility.py -k "slider or label and chromium"` → FAIL: no slider named "Volumen" (count 0; experimentos/22 §5), `label_mismatch` lists `Volumen` and `Nivel`, per-speaker sliders have no `aria-valuetext`.

- [ ] **Step 3: Implement**

`index.html`: the wrappers become `<div class="field …">` holding `<span><label for="volume">Volumen</label> <output id="volume-out" for="volume" aria-hidden="true" …></output></span>` and the input (same for `monitor-gain`, keeping its `aria-label="Nivel del monitor"`). The `<output>` is `aria-hidden` because the slider's `aria-valuetext` carries the value; otherwise the implicit `status` role announces it a second time. `app.js`: call `setValueText` wherever `#volume-out`, `#monitor-gain-value` are written, and for `#spatial-character` on sync and on `input`. Per-speaker sliders: `liveRange` writes the spoken text with `setValueText` on every `input`; every place that writes a `rangeCell` value goes through `cell.show(v)`, so the visible and spoken values never disagree. Their accessible names (the existing `aria-label`s) stay as they are.

- [ ] **Step 4: Run to see them pass** (same command) → PASS; Task 1's axe test still PASS.

- [ ] **Step 5: No commit.**

---

### Task 3: Parlantes and the presets list update in place and keep the focus (group 1, item 2, plus extension (b) of 2026-10-08)

**Files:**
- Modify: `host/src/aurasync/panel/app.js:1088-1150` (`deviceRow`, `renderDevices`), `:706-721` (`#room-hint` in `renderRoom`), `:2797-2818` (`renderSpatial`)
- Modify (extension b): `app.js:2251-2273` (`renderPresets`: today the whole `#presets` list is rebuilt whenever its key changes, and the key includes `s.preset`, which "Cargar" changes)
- Test: `host/tests_browser/test_panel_accessibility.py`

**Interfaces:**
- Consumes: `open_themed`, `busy`, `show` (Task 1).
- Produces (`app.js`): `const deviceRows = new Map()` keyed by `address`; `deviceRow(d) -> { tr, name, state, actions, actionsKey }`; `syncDeviceRow(row, d, playing): void`; `const spatialRows = new Map()` keyed by speaker name; `const presetRows = new Map()` keyed by preset name, each `{ li, name, load, del }`, with the "· actual" text, the `current` class and `load.disabled` (A/B running) updated in place. `#room-hint` is created once with a text `<span>` and one persistent "Usar «Automático»" button. Task 7 relies on `#room-hint` being written only when its text changes.

- [ ] **Step 1: Write the failing tests**

```python
def test_no_parlantes_control_is_rebuilt_while_nothing_changes(browser, svc):
    page = open_themed(browser, svc); busy(page); show(page, "parlantes")
    page.evaluate(TAG_INTERACTIVE)            # window.__tagged = visible interactive elements of the view
    page.wait_for_timeout(3000)
    assert page.evaluate("() => window.__tagged.filter(e => !e.isConnected).length") == 0   # audit: 13 of 74

@pytest.mark.parametrize("control", ["Desconectar", "Usar «Automático»", "Ambiental"])
def test_focus_stays_on_a_parlantes_control_for_3_s(browser, svc, control):
    ...  # keyboard modality (press Shift), focus the first visible match, wait 3000 ms
    assert page.evaluate("() => document.activeElement === window.__f && window.__f.isConnected")

def test_disconnecting_with_the_keyboard_keeps_focus_in_the_row(browser, svc):   # Review Focus 1
    ...  # focus "Desconectar" of NAMES[0]'s device row, press Enter, wait until the row shows "Conectar"
    assert page.evaluate("() => document.activeElement.closest('tr') === window.__row")

def test_focus_stays_on_cargar_after_loading_a_preset(page, svc):            # extension b
    svc.command("preset_save", name="cerrado"); svc.command("preset_save", name="amplio")
    # keyboard modality; focus "Cargar" of "cerrado"; Enter; wait until its row says "· actual"; wait 3000 ms
    assert page.evaluate("() => document.activeElement === window.__f && window.__f.isConnected")
    # the "Deshacer" notice that loading offers is not a reason to move focus: it must not take it
```

This test is the measurement as well. The code suggests (INFERIDO) that the list is rebuilt and focus lands on `body`, but this was never measured. Record what Step 2 shows. If it already passes, stop the extension (b) part here and report it as "not reproduced"; do not change `renderPresets`.

- [ ] **Step 2: Run to see them fail** → FAIL: 13 disconnected; focus on `body` for all three controls (experimentos/22 §4.2); "Cargar": expected (INFERIDO) focus on `body` once the list is rebuilt. Note the measured result for the changelog and for experimentos/22 (Task 13).

- [ ] **Step 3: Implement**

Device rows keyed by address, like `renderServices` (`app.js:1183-1231`). Rebuild a row's actions only when `actionsKey = connected|paired|busy|in_installation|playing` changes. If focus was inside the old actions, focus the new first button, or else the row's name cell (`tabindex="-1"`). Group header rows are keyed by group. Re-append rows **only when the order differs**, because moving a focused node blurs it. `#room-hint`: build it once, then update its text with `setText` and toggle `hidden`. Spatial rows: keyed, `aria-pressed` updated in place. Presets: keyed rows the same way. A row is created only for a new name and removed only for a gone (or hidden, being deleted) name, and the list is re-ordered only when the order differs. The "Sin presets guardados." line is a single persistent `<li>` toggled with `hidden`. The A/B selects (`:2274-2280`) keep rebuilding their options as today: the `<select>` itself is not replaced, so it keeps its focus.

- [ ] **Step 4: Run to see them pass** → PASS; also `tests_browser/test_panel_virtual.py -k chromium`, `test_panel_spatial.py -k chromium` and `test_panel_usability.py -k "preset and chromium"` (the undo tests that load and delete presets) stay green.

- [ ] **Step 5: No commit.**

---

### Task 4: The `(?)` help popovers meet 1.4.13 (group 1, item 3)

**Files:**
- Modify: `host/src/aurasync/panel/tailwind.input.css:106-109` (`.help`, `.help-btn`, `.help-pop`), then `hatch run web:css`
- Modify: `host/src/aurasync/panel/app.js` (new `setupHelp()`, called from `setup()` at `:2530-2535`)
- Test: `host/tests_browser/test_panel_accessibility.py`
- Not modified: `host/web/src/chain/ParamControl.tsx:32-44` (it renders the same `.help > .help-btn + .help-pop` markup, which document-level listeners cover)

**Interfaces:**
- Consumes: `open_themed`, `show`, `install_color`, `ratio` (Task 1).
- Produces: `setupHelp(): void`. It uses delegated `keydown` (Escape), `mouseout` and `focusout` listeners on `document` and the attribute `data-dismissed` on `.help`.

- [ ] **Step 1: Write the failing tests** (parametrized over `view` ∈ `ajustes`, `cadena`; `scheme` ∈ `light`, `dark` where noted)

```python
def test_escape_closes_a_help_opened_by_the_pointer(browser, svc, view):
    btn.hover(); expect visibility(pop) == "visible"; page.keyboard.press("Escape")
    assert visibility(pop) == "hidden"            # pointer still over the button

def test_escape_closes_a_help_opened_by_focus(browser, svc, view):
    keyboard focus btn; Escape -> hidden; document.activeElement is still btn

def test_the_pointer_can_travel_from_the_button_onto_the_help(browser, svc, view):
    btn.hover(); page.mouse.move(<pop centre>, steps=10); assert visibility(pop) == "visible"

def test_the_help_focus_ring_reaches_3_to_1(browser, svc, scheme):   # light measured 2.71
    assert contrast(btn, "outline-color") >= 3.0

def test_a_dismissed_help_opens_again_and_a_free_escape_passes_through(browser, svc):   # Review Focus 3
    Escape on hover -> hidden; mouse away and back -> visible
    with no help open: page.evaluate(listen for keydown) ; Escape ; defaultPrevented is False
```

- [ ] **Step 2: Run to see them fail** → FAIL: visible after Escape (both ways), hidden after crossing the 8 px gap, ring 2.71 in light (experimentos/22 §2.3, §4.2).

- [ ] **Step 3: Implement**

CSS: give `.help-pop::before` an invisible bridge over the gap between the button and the popover (absolute, `bottom: 100%`, full width, `height: 0.5rem`). Add `.help[data-dismissed] .help-pop { invisible opacity-0 }`, placed after the `:hover`/`:focus-within` rule so it wins. Ring: `focus-visible:outline-sky-600` (3.96–4.02 light, 4.40 dark, measured for the other rings). JS: on Escape, mark every `.help` that is `:hover` or contains `document.activeElement`, and call `preventDefault` only if one was marked. Clear the mark on `mouseout`/`focusout` when `relatedTarget` leaves that `.help`.

- [ ] **Step 4: Run to see them pass** → PASS; `test_panel_chain.py -k chromium` stays green.

- [ ] **Step 5: No commit.**

---

### Task 5: Text contrast: grey text in dark and the primary buttons (group 1, item 4, text part)

**Files:**
- Modify: `host/src/aurasync/panel/tailwind.input.css` — add `dark:text-zinc-400` to the eight rules without it: `.table th` (:78), `.room-front` (:156), `.room-custom` (:157), `.meter-scale` (:182), `.log time, .log-service` (:187), `.chart-empty` (:197), `.spk-toggle-mark` (:333), `.nav-tab` (:371). Also in dark: `svg .axis-label` (after :375; same colour, same defect, which axe does not see in SVG). `.btn-primary` (:36): `bg-sky-700 border-sky-700 hover:bg-sky-800`, in both themes. Then `hatch run web:css`.
- Modify: `host/tests_browser/test_panel_accessibility.py` (`KNOWN` shrinks)

**Interfaces:**
- Consumes: `KNOWN`, `contrast`, `open_themed` (Task 1).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test** — remove every `color-contrast` entry from `KNOWN` (light and dark, pc and phone), and add:

```python
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_primary_buttons_keep_4_5_to_1_also_on_hover(browser, svc, scheme):
    for b in visible .btn-primary in Escuchar and Calibrar (#run, #cal-run, #est-apply when shown):
        assert contrast(b) >= 4.5; b.hover(); assert contrast(b) >= 4.5     # white on sky-600 measured 4.06
```

- [ ] **Step 2: Run to see it fail** → `test_axe_finds_only_the_known_violations` FAIL with `color-contrast` in every dark screen (5–286 nodes) and Calibrar light; hover test FAIL (4.06, and white on sky-500 on hover).
- [ ] **Step 3: Implement** the CSS above; `hatch run web:css`.
- [ ] **Step 4: Run to see them pass** → axe matrix PASS with `color-contrast` gone from `KNOWN` (forced-colors entries untouched); hover test PASS.
- [ ] **Step 5: No commit.**

---

### Task 6: Non-text contrast: field borders, level meters and the other light-mode graphics (group 1, item 4, graphics part, plus extension (c) of 2026-10-08)

**Files:**
- Modify: `host/src/aurasync/panel/tailwind.input.css:54-55` (fields: `border-zinc-500 dark:border-zinc-500`), `:173-177` (meter: custom properties `--meter-ok`, `--meter-warn`, `--meter-hot`, `--meter-empty`, `--meter-edge` on `.meter-track`, used by the gradient, the mask and a 1 px edge; `forced-color-adjust: none` on the track). Then `hatch run web:css`.
- Modify (extension c), light-mode values only, dark left as it is: `tailwind.input.css:79-81` (spectrum `.input-bars`/`.input-bar`), `:85` (`.corr-track` gradient ends), `:226-227` (`.mic-track`/`.mic-window`), `:130-133` (`.status-dot` colours), `:258-265` (`.sync-zone` colours), `:59` (`input[type="range"]`: a styled track, see Step 3b)
- Modify (extension c, only if the slider fill needs it): `app.js` — one `setRangeFill(input)` helper that sets `--fill` (0–100 %) on an `input[type=range]`, called from a delegated `input` listener and from `syncValue`'s write path
- Test: `host/tests_browser/test_panel_accessibility.py`

**Interfaces:**
- Consumes: `open_themed`, `busy`, `show`, `ratio`, `contrast`, `install_color` (Task 1).
- Produces: the five `--meter-*` custom properties (read by the tests); `--fill` on range inputs if Step 3b needs it.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_field_border_reaches_3_to_1(browser, svc, scheme):     # measured 1.47–1.72
    for each view: for each visible select, input[type=text], input[type=number] (header "Fuente", "Modo" included):
        assert contrast(field, "border-top-color") >= 3.0, field description

@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_level_meter_zones_and_track_reach_3_to_1(browser, svc, scheme):
    v = computed --meter-* of the first .meter-track; card = bgOf(track)
    assert ratio(v.ok, v.empty) >= 3 and ratio(v.warn, v.empty) >= 3 and ratio(v.hot, v.empty) >= 3   # light: 2.60, 1.69
    assert ratio(v.edge, card) >= 3                                                                  # track end: 1.27

def test_the_level_meter_survives_forced_colors(browser, svc):            # Review Focus 5
    forced context, playing; screenshot the first .meter-track; the two dominant colours differ by >= 3:1
```

Candidate values, to be confirmed by the test because Tailwind 4 palette values are `oklch`: light `ok` green-700, `warn` amber-700, `hot` rose-600, `empty` zinc-200, `edge` zinc-500; dark keeps today's (green-600, amber-500, rose-600, zinc-800) with `edge` zinc-500. The peak marker must stay ≥ 3:1 against `ok` (dark measured exactly 3.00): add that assertion too.

Extension (c): each graphic is asserted at ≥ 3:1 in the light theme, against what it must be told apart from. The value after `#` is what experimentos/22 §2 measured:

```python
def test_light_graphics_reach_3_to_1(browser, svc):                      # light context, busy(page), meters running
    # Escuchar, input card
    assert contrast(".input-bar", "background-color") >= 3              # vs its .input-bars background; 2.46
    corr = ".corr-track": both gradient ends vs the card                # 1.52 (green end) / 1.92 (red end); read as
                                                                        # custom properties --corr-low/--corr-high, as the meter does
    # Escuchar, "now" card
    for zone in .sync-zone (fused, first, double, haas): zone background vs the card >= 3   # 1.95 / 2.13 / 2.47
    # Parlantes and Diagnóstico
    for dot in visible .status-dot (each status class present): dot background vs its row's background >= 3   # 2.47 / 2.62
    # Calibrar (microphone check shown: Calibrar -> the microphone level, as test_panel_usability's mic tests do)
    assert ratio(.mic-window background over .mic-track, .mic-track background) >= 3   # 1.15

def test_the_slider_track_is_visible_in_light(browser, svc):
    # pixel sampling, as experimentos/22 §2.2 did: screenshot #volume at value -30 (half), dominant colours
    # empty part of the track vs the card >= 3                          # Chromium's #efefef: 1.13
    # filled part and thumb vs the card >= 3, and filled vs empty >= 3  # sky-600: 3.96–4.02 today
    # the slider still moves with the arrows and the pointer (value changes), and its box is still >= 24 px high
```

Candidate light values (Tailwind 4 is `oklch`, so the test decides): spectrum bar sky-700; correlation ends red-600 and emerald-700; `.mic-window` emerald-600 at full opacity; status dots emerald-700 / zinc-500 (amber and rose dots are checked too); sync zones emerald-700, lime-700, amber-700 and the `haas` zone's equivalent. The dark values stay. Correlation (1.84/1.77) and the microphone window (1.54) also fail in dark, but the user's answer covers light only: list them in Task 13's report, do not fix them here.

- [ ] **Step 2: Run to see them fail** → custom properties absent (read as empty), the border ratios 1.47–1.72, and every extension (c) value as measured above.
- [ ] **Step 3: Implement** the meter and field CSS, and the extension (c) colours; `hatch run web:css`.
- [ ] **Step 3b: The slider track.** `accent-color` paints only the filled part, so Chromium's empty track (#efefef) cannot be fixed without styling the track. Give `input[type="range"]` `appearance: none`. The track (`::-webkit-slider-runnable-track`, `::-moz-range-track`) gets a light fill and a 1 px zinc-500 edge (≥ 3:1 against the card). The thumb (`::-webkit-slider-thumb`, `::-moz-range-thumb`) is a sky-600 circle of at least 16 px. The input box stays `h-11 sm:h-7`, so the 24 px target is unchanged. The filled part is kept: in Firefox through `::-moz-range-progress`; in Chromium through a `linear-gradient` driven by `--fill`, written by `setRangeFill`. Apply this in both themes, because a styled track replaces the native one everywhere; the test asserts light, and dark must not regress (the axe matrix and Task 5's tests stay green). Put `forced-color-adjust: auto` on the thumb, so it still shows in forced colours. If this part grows past these rules plus the one helper, stop and report: it would then deserve its own change.
- [ ] **Step 4: Run to see them pass**; `tests_browser/test_panel_quality.py -k chromium` (meters, reduced motion), `test_panel_chain.py -k chromium` (many sliders) and `test_panel_usability.py -k "eight and chromium"` (slider widths on phone and PC) stay green.
- [ ] **Step 5: No commit.**

---

### Task 7: Live regions written only when they change (group 1, item 5)

**Files:**
- Modify: `host/src/aurasync/panel/app.js:2999-3003` (`#monitor-state`: `setText`; the changing numbers — cushion, refills, trims, matched level — move to a new non-live `#monitor-detail`), `:3053-3054` (`#engine-state`: `setText`), `:1739-1742` (`#radio-note`: the "desde hace …" duration moves to a new non-live `#radio-since`)
- Modify: `host/src/aurasync/panel/index.html:162` (add `<p id="monitor-detail" class="muted small">` after `#monitor-state`), `:395` (add `<span id="radio-since">` outside the `role="status"` paragraph)
- Test: `host/tests_browser/test_panel_accessibility.py`

**Interfaces:**
- Consumes: `rewrites`, `busy`, `show` (Task 1); `#room-hint` written once per change (Task 3).
- Produces: `monitorDetailText(m): string`, split out of `monitorStateText` (`app.js:2942-2951`), which keeps only the state sentence.

- [ ] **Step 1: Write the failing tests**

```python
LIVE = {"escuchar": "#monitor-state", "parlantes": "#room-hint", "ajustes": "#engine-state", "diagnostico": "#radio-note"}

def test_live_regions_are_not_rewritten_with_the_same_text(browser, svc):
    page = open_themed(browser, svc); busy(page)   # + monitor binaural to "simulated_headphones", as the audit
    for view, sel in LIVE.items():
        show(page, view); assert rewrites(page, [sel], 5000)[sel] == 0     # measured 60/min each

def test_no_status_region_holds_a_value_that_ticks(browser, svc):
    # every [role=status] / [aria-live] in all views, over 5 s with nothing touched: 0 rewrites
    # (the #undo and #toast notices are excluded: they are empty and hidden here)

def test_a_live_region_still_changes_when_its_text_does(browser, svc):   # Review Focus 4
    monitor mode off -> binaural; #monitor-state text changes, and rewrites over the next 3 s == 0
```

- [ ] **Step 2: Run to see them fail** → 5 rewrites in 5 s for `#monitor-state`, `#engine-state` and `#radio-note` (`#room-hint` already 0 after Task 3).
- [ ] **Step 3: Implement** as listed. Keep the copy unchanged, only split in two.
- [ ] **Step 4: Run to see them pass**; `test_panel_monitor.py -k chromium` and `test_panel_engine.py -k chromium` stay green (they may read `#monitor-state` text: update their expectations to `#monitor-detail` where the moved numbers are asserted, and nothing else).
- [ ] **Step 5: No commit.**

---

### Task 8: The "Deshacer" notice pauses, returns focus and does not hide the focus (group 1, item 6)

**Files:**
- Modify: `host/web/src/undo.ts:139-146` (`Offer`), `:171-237` (`createUndo`), plus a new exported `countdown`
- Test: `host/web/test/undo.test.ts` (vitest, fake timers; there is no DOM in vitest here, so the timing logic must be pure)
- Modify: `host/src/aurasync/panel/app.js:2222-2226` (`deferUndo` gains `focusAfter`), `:2268` (preset: `() => $("preset-name")`), `:1114` (forget device: `() => $("scan")`)
- Modify: `host/src/aurasync/panel/tailwind.input.css:384-385` (`html:has(#undo:not([hidden]))` raises `scroll-padding-bottom` by the notice's height plus its offset; `body:has(#undo:not([hidden])) #views` gets the same extra bottom padding)
- Test: `host/tests_browser/test_panel_usability.py` (next to the undo tests, `:57-146`)
- Rebuild: `npm run check && npm test && npm run build`, `hatch run web:css`, stamp check

**Interfaces:**
- Consumes: `UNDO_MS`, `createUndo(box, send)`; `window.aurasync.undo.setDuration(ms)` (used by the tests).
- Produces:
  - `export interface Countdown { hold(): () => void; cancel(): void; remaining(): number }`
  - `export function countdown(ms: number, onExpire: () => void, now: () => number = Date.now): Countdown` — expires once no hold is active and the unheld time adds up to `ms`; each `hold()` returns its own release, and calling it twice is harmless.
  - `Offer.focusAfter?: () => HTMLElement | null`
  - `deferUndo(message, commit, revert, focusAfter = null)` in `app.js`.

- [ ] **Step 1: Write the failing tests**

```ts
describe("the countdown", () => {
  it("expires after ms without holds");
  it("does not expire while held, and resumes with what was left");   // hold at 4 s, wait 30 s, release -> expires 6 s later
  it("holds stack: expires only after every hold is released");       // Review Focus 2 (pointer + focus)
  it("a release called twice does not release another hold");
  it("cancel stops it for good");
});
describe("the notice", () => { it("a new offer while held commits the old one"); });  // Review Focus 2, via a fake box object
```

```python
def test_the_undo_notice_waits_while_the_pointer_is_on_it(page, svc):      # measured: 10.3 s, did not stop
    setDuration(1500); delete preset "cine"; hover #undo; wait 3000 -> still visible; "cine" still in svc.state()
    mouse away -> hidden within 3 s; "cine" deleted

def test_the_undo_notice_waits_while_it_has_focus(page, svc): ...           # same, with keyboard focus on "Deshacer"

def test_deleting_with_the_keyboard_puts_focus_on_undo_and_brings_it_back(page, svc):
    focus "Borrar", Enter -> document.activeElement is #undo [data-undo-button]   # measured: body, 13 Tabs away
    Enter -> "Deshecho"; document.activeElement is #preset-name

def test_the_undo_notice_does_not_hide_the_focused_control(page, svc):      # 2.4.11; it covered "Tono", "Silenciar"
    offer a notice; keyboard-focus each [data-card=quick] .q-tone and .q-mute
    assert the focused rect does not intersect #undo's rect
```

- [ ] **Step 2: Run to see them fail**

Run: `cd host/web && npx vitest run test/undo.test.ts` → FAIL (`countdown` not exported). Browser tests → FAIL as measured.

- [ ] **Step 3: Implement** `countdown`. `createUndo` takes a hold on `pointerenter`/`focusin` of the box and releases it on `pointerleave`/`focusout` (when focus leaves the box). While held, the bar is frozen: `transition: none`, `scaleX(remaining/duration)`. On release it runs to 0 over `remaining`. `offer()` moves focus to the button when `document.activeElement` is `body` or `null`. The "Deshacer" click, and any hide with focus inside the box, focus `focusAfter()` if it returns a connected element. CSS as listed.

- [ ] **Step 4: Run to see them pass**: vitest; then the browser tests and all of `test_panel_usability.py -k chromium` (the existing undo tests must stay green). The build and the stamp check pass.
- [ ] **Step 5: No commit.**

---

### Task 9: Diagnóstico: the Servicios table fits its column (group 1, item 7a)

**Files:**
- Modify: `host/src/aurasync/panel/tailwind.input.css:135` (`.speaker-sub`: `break-all` → `break-words`), and a new `.services-table .cell-actions .hstack { flex-wrap: wrap }`. Then `hatch run web:css`.
- Test: `host/tests_browser/test_panel_accessibility.py`

**Interfaces:** Consumes `open_themed`, `busy`, `show`. Produces nothing.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("width", [1366, 1440, 1920])
def test_the_services_table_fits_and_no_word_is_split(browser, svc, width):   # measured 609 px in 582
    table = .services-table; wrap = its .table-wrap
    assert table.scrollWidth <= wrap.clientWidth
    for every button in .services-table: its rect lies inside the card's rect
    assert computed word-break of .services-table .speaker-sub != "break-all"
```

- [ ] **Step 2: Run to see it fail** → 609 > 582 at 1440 (experimentos/22 §7.1).
- [ ] **Step 3: Implement**: the two CSS changes, in this order, stopping at the first that passes. If both still overflow, report back rather than changing the Diagnóstico zones. Phone (390 px) is not asserted: there the table keeps scrolling inside its wrap.
- [ ] **Step 4: Run to see it pass**; the `test_eight_speakers_fit_every_screen` tests in `test_panel_usability.py` stay green (`.speaker-sub` is shared with the speakers table).
- [ ] **Step 5: No commit.**

---

### Task 10: The per-speaker delay reads with a decimal comma (group 1, item 7b)

**Files:**
- Modify: `host/src/aurasync/panel/app.js:776-778` (`speakerRow`: the delay input), `:919` (`renderSpeakers`: `syncValue(row.delay, …)`)
- Test: `host/tests_browser/test_panel_accessibility.py`

**Interfaces:**
- Produces: `parseDecimal(text: string): number | null` in `app.js` (accepts `,` or `.`; `null` if it is not a finite number); the delay field is `type="text" inputmode="decimal"` with `aria-label` unchanged. It shows `nf(v, 2)`, and ArrowUp/ArrowDown step 0.1 within [0, 100].

- [ ] **Step 1: Write the failing test**

```python
def test_the_detail_delay_reads_and_takes_a_decimal_comma(page, svc):     # measured "4.501"
    svc.command("set", speaker=NAMES[0], changes={"delay_ms": 4.501}); show(page, "parlantes")
    expect(delay_of(NAMES[0])).to_have_value("4,50")
    delay.fill("6,25"); delay.press("Enter")  -> svc delay_ms == 6.25
    delay.fill("abc"); delay.press("Enter")   -> aria-invalid="true"; svc delay_ms still 6.25
    delay.press("ArrowUp")                    -> svc delay_ms == pytest.approx(6.35)
```

- [ ] **Step 2: Run to see it fail** → value `"4.501"`.
- [ ] **Step 3: Implement** as in Interfaces. The field is still disabled while the loop runs (`:920`).
- [ ] **Step 4: Run to see it pass**; `test_panel.py -k "delay and chromium"` and `test_panel_usability.py -k chromium` stay green.
- [ ] **Step 5: No commit.**

---

### Task 11: A simulated calibration never reports a negative latency (group 1, item 7c)

**Files:**
- Test: `host/tests/test_panel_ops.py` (next to `test_calibration_and_saving_it`, `:153`)
- Modify: where the root cause is. Candidates: `host/src/aurasync/simulated.py:197-226` (`SimulatedMicrophone` timing, if the simulated room has no device latency), or `host/src/aurasync/session.py:270-276` (the `latency_ms` formula, which would also affect real hardware). **`session.py` is never changed by this task** (see the stop condition in Step 3).

**Interfaces:** Produces nothing new; `state["latency"]["measured_ms"]` keeps its meaning (`snapshot.py:118`).

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("virtual", [False, True], ids=["three", "three-plus-virtual"])   # the audit had a virtual one
def test_a_simulated_calibration_reports_a_latency_that_is_not_negative(svc, virtual):
    s = svc(); (ok(s, op="speaker_add_virtual") if virtual); ok(s, op="start"); ok(s, op="calibrate", seconds=5.0)
    wait(done, 30); assert ok(s, op="state")["latency"]["measured_ms"] >= 0      # the panel showed "-27 ms"
```

- [ ] **Step 2: Run to see it fail** → `cd host && hatch test tests/test_panel_ops.py -k latency_that_is_not_negative`, negative. If both cases pass, stop and report: the audit's −27 ms then came from a state this test does not reproduce.
- [ ] **Step 3: Find the cause before fixing** (superpowers:systematic-debugging). Write down the evidence: which term of `latency_ms = desfase_grueso_ms − (before/rate − MIC_SLACK_S)·1000` goes wrong, and why.
  - **If the cause is in the simulation** (`simulated.py`, e.g. a simulated microphone or room without the device latency a real chain has): fix it there and go on to Step 4.
  - **STOP condition (user, 2026-10-08): if the cause is in `session.py`'s formula** (or anything else on the real calibration path), do not change it. End the task: report to the controller the cause, the evidence, and the proposed change with its expected effect on a real calibration. The controller asks the user before anyone touches it. Leave the failing test in place, marked `xfail(strict=True, reason="cause in session.py, waiting for the user's decision: <one line>")`, so the defect stays visible and the suite stays green.
  - In no case clamp it in the panel: a negative measured latency on real hardware is a measurement error that must stay visible ("la sincronización se mide, no se supone").
- [ ] **Step 4: Run to see it pass** (simulation cause only); `hatch test tests/test_panel_ops.py tests/test_virtual_speakers.py` stays green.
- [ ] **Step 5: No commit.**

---

### Task 12: No layout shift on load (group 1, item 7d)

**Files:**
- Modify: `host/src/aurasync/panel/tailwind.input.css` (new rule `body:not([data-ready]) #views { visibility: hidden; }`), and, only if the test still fails after it, min-heights in `index.html:26-56` (`#player` chips: `#latency-total`, `#chip-quality`) for the shifting nodes the test names. Then `hatch run web:css`.
- Test: `host/tests_browser/test_panel_accessibility.py`

**Interfaces:** Consumes `open_themed`. Produces nothing. `body[data-ready]` is set by the first `render()` (`app.js:522`).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("size", ["pc", "phone"])
def test_the_panel_does_not_shift_while_it_loads(browser, svc, size):
    page = fresh context, goto, wait body[data-ready='1'] + 3000 ms
    shifts = page.evaluate(CLS_JS)   # PerformanceObserver('layout-shift', buffered) without hadRecentInput: sum and sources
    assert shifts["sum"] <= 0.1, shifts["sources"]

def test_the_views_appear_when_the_stream_is_blocked(browser_page_without_stream):
    expect(browser_page_without_stream.locator("[data-view]:not([hidden])")).to_be_visible(timeout=10000)
```

- [ ] **Step 2: Run to see it fail** → expected ≈ 0.5 (Lighthouse 0.513 desktop, 0.788 mobile). If it already passes before the fix, stop and report: headless Playwright then does not reproduce Lighthouse's shift, and the method or the threshold needs the user's decision.
- [ ] **Step 3: Implement** the hidden-until-ready rule. Add min-heights only for the sources still named.
- [ ] **Step 4: Run to see them pass**; `tests_browser/test_pwa.py -k chromium` and `test_panel.py -k chromium` stay green (the PWA's connect screen lives outside `#views`).
- [ ] **Step 5: No commit.**

---

### Task 13: Close: both browsers, re-measured numbers, documents and log

**Files:**
- Modify: `docs/research/10-panel-de-control.md` §10. Group 1 rows: "aplicado (2026-10-xx)" with the test that guards each, plus a line for the three extensions of 2026-10-08 (per-speaker sliders, "Cargar", the other light-mode graphics); replace "Nada está aplicado" with what is.
- Modify: `docs/research/experimentos/22-auditoria-medida-del-panel.md`: a new section "Después del grupo 1 (fecha)" with the after-values (MEDIDO), the machine, Chromium and Firefox versions and axe-core 4.14.0. Also record the first forced-colors axe result (it was never measured before).
- Modify: `docs/roadmap.md` (group 1 done; first check whether another session is editing it), `.claude/logs/agent-changelog.md` (an entry on top, in the format at the end of that file, including each "seen red" run and anything that went wrong)

- [ ] **Step 1: Full runs**, once the machine is free: `cd host && hatch run browser:test` (Chromium and Firefox, everything); `cd host/web && npm run check && npm test`; `python3 host/scripts/web_stamp.py`; `PY=python3.12 scripts/check.sh` → `check: ok`.
- [ ] **Step 2: Collect the after-values** from the new tests, run with `-s` on Chromium (each test prints what it measured): contrast ratios (the extension (c) graphics and the slider track included), rewrites per region, focus after 3 s (the "Cargar" measurement before and after included), notice hold times, CLS, table width. Write them into experimentos/22 next to the before-values.
- [ ] **Step 3: Update research/10 §10, the roadmap and the changelog.** Do not delete `probes/23-auditoria-panel/`: groups 2–3 are still open.
- [ ] **Step 4: No commit.** Report to the user: what was applied, and what is left for them. That includes Task 11's cause if it stopped, extension (b) if "Cargar" did not reproduce, the dark-mode failures outside scope (correlation 1.84/1.77, microphone window 1.54) and the forced-colors baseline.

---

## Open questions — answered (user, 2026-10-08)

1. **axe in `scripts/check.sh`.** Answered: the full axe matrix runs in the browser tests (`hatch run browser:test`); `check.sh` carries only the cheap checks (pinned version, dev-only, not shipped). Recorded in Global Constraints as the reading of d-7c8794-a5f3ba.
2. **`aria-valuetext` on the per-speaker sliders.** Answered: in scope, folded into Task 2 (extension a) with its test.
3. **The other light-mode graphics under 3:1** (spectrum, correlation, microphone window, status dots, sync bar, Chromium's empty slider track). Answered: in scope, added to Task 6 (extension c), each asserted at ≥ 3:1 against its measured value.
4. **The negative latency's cause.** Answered: investigate first. If the cause is in `session.py`'s formula and not in the simulation, Task 11 stops and reports the cause and the proposed change to the controller, who asks the user before anyone touches it (Task 11, Step 3).
5. **Focus lost on "Cargar" a preset.** Answered: in scope, folded into Task 3 (extension b). The test measures it first, then the list is fixed in place like Parlantes, with the 3 s focus assertion.

## Assumptions that stand

6. **forced-colors baseline.** Task 1 produces the first forced-colors axe result ever. Whatever it finds enters `KNOWN` as a baseline, not as a fix; the user decides when to fix it.
7. **Firefox and `forced_colors`** (ASSUMPTION: Playwright supports it only in Chromium). Task 1 detects it in the page (`matchMedia('(forced-colors: active)')`) and skips forced-colors on Firefox with that reason.
