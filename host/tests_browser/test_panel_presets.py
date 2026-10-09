"""The presets card: rename, confirmed deletion and the expandable summary (roadmap i-7c8794-dcbd24)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from playwright.sync_api import Page, expect

if TYPE_CHECKING:
    from tests_browser.test_panel import Running


def row(page: Page, name: str):
    return page.locator("#presets li", has=page.locator(".preset-name", has_text=name))


def test_renaming_a_preset(page: Page, svc: Running):
    svc.command("preset_save", name="cine")
    svc.command("preset_save", name="musica")
    expect(page.locator("#presets li")).to_have_count(2)
    row(page, "cine").get_by_role("button", name="Renombrar").click()
    dialog = page.get_by_role("dialog")
    field = dialog.get_by_label("Nuevo nombre del preset")
    expect(field).to_be_focused()
    # An existing name is refused before the server sees it; the dialog stays open.
    field.fill("musica")
    dialog.get_by_role("button", name="Guardar").click()
    expect(dialog).to_contain_text("Ya hay un preset llamado")
    field.fill("   ")
    dialog.get_by_role("button", name="Guardar").click()
    expect(dialog).to_contain_text("Escribí un nombre")
    field.fill("pelicula")
    dialog.get_by_role("button", name="Guardar").click()
    expect(dialog).to_be_hidden()
    expect(row(page, "pelicula")).to_have_count(1)
    expect(row(page, "cine")).to_have_count(0)
    assert sorted(svc.state()["presets"]) == ["musica", "pelicula"]
    # Focus stays on the renamed row's button.
    expect(row(page, "pelicula").get_by_role("button", name="Renombrar")).to_be_focused()


def test_cancelling_a_rename_changes_nothing(page: Page, svc: Running):
    svc.command("preset_save", name="cine")
    expect(page.locator("#presets li")).to_have_count(1)
    row(page, "cine").get_by_role("button", name="Renombrar").click()
    page.get_by_role("dialog").get_by_role("button", name="Cancelar").click()
    expect(page.get_by_role("dialog")).to_be_hidden()
    assert svc.state()["presets"] == ["cine"]


def test_deleting_a_preset_asks_first(page: Page, svc: Running):
    svc.command("preset_save", name="cine")
    expect(page.locator("#presets li")).to_have_count(1)
    row(page, "cine").get_by_role("button", name="Borrar").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("¿Borrar el preset «cine»?")
    expect(dialog.get_by_role("button", name="Cancelar")).to_be_focused()
    dialog.get_by_role("button", name="Cancelar").click()
    expect(dialog).to_be_hidden()
    expect(row(page, "cine")).to_have_count(1)
    assert page.locator("#undo").is_hidden()
    assert svc.state()["presets"] == ["cine"]
    page.evaluate("window.aurasync.undo.setDuration(800)")
    row(page, "cine").get_by_role("button", name="Borrar").click()
    page.get_by_role("dialog").get_by_role("button", name="Borrar").click()
    expect(row(page, "cine")).to_have_count(0)
    # With no row left, the focus goes to the name field.
    expect(page.locator("#preset-name")).to_be_focused()
    expect(page.locator("#undo")).to_contain_text("Preset «cine» borrado")
    expect(page.locator("#undo")).to_be_hidden(timeout=5000)
    page.wait_for_timeout(500)
    assert svc.state()["presets"] == []


def test_the_preset_card_expands_to_show_its_configuration(page: Page, svc: Running):
    svc.command("chain_set", stage="spatial", algorithm="front")
    svc.command("chain_set", stage="diffuse", algorithm="noise_tail", params={"level_db": -9.0, "rt60_s": 0.8})
    svc.command("preset_save", name="sala")
    svc.command("preset_save", name="otro")
    expect(page.locator("#presets li")).to_have_count(2)
    li = row(page, "sala")
    toggle = li.locator("button[aria-expanded]")
    detail = li.locator("[data-preset-detail]")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(detail).to_be_hidden()
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(li.get_by_role("button", name="Ocultar el detalle del preset sala")).to_have_count(1)
    expect(detail).to_contain_text("Modo espacial: Frente intacto")
    expect(detail).to_contain_text("Difusión: Cola de ruido")
    expect(detail).to_contain_text("Nivel -9 dB")
    expect(detail).to_contain_text("Largo 0,80 s")
    expect(detail).to_contain_text("Retardo de los traseros")
    expect(detail).to_contain_text(svc_speaker_name(svc))
    # A stage the preset keeps untouched says so.
    expect(detail).to_contain_text("por defecto")
    toggle.click()
    expect(detail).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")


def svc_speaker_name(svc: Running) -> str:
    return svc.state()["speakers"][0]["name"]


def test_escape_closes_the_dialogs_without_changes(page: Page, svc: Running):
    svc.command("preset_save", name="cine")
    expect(page.locator("#presets li")).to_have_count(1)
    row(page, "cine").get_by_role("button", name="Renombrar").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_be_hidden()
    row(page, "cine").get_by_role("button", name="Borrar").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_be_hidden()
    expect(row(page, "cine")).to_have_count(1)
    assert svc.state()["presets"] == ["cine"]
    assert page.locator("#undo").is_hidden()


def test_deleting_a_preset_moves_the_focus_to_a_neighbour(page: Page, svc: Running):
    for name in ("uno", "dos", "tres"):
        svc.command("preset_save", name=name)
    expect(page.locator("#presets li")).to_have_count(3)
    row(page, "dos").get_by_role("button", name="Borrar").click()
    page.get_by_role("dialog").get_by_role("button", name="Borrar").click()
    expect(row(page, "dos")).to_have_count(0)
    expect(row(page, "tres").get_by_role("button", name="Borrar")).to_be_focused()
    # The last row hands the focus to the previous one.
    row(page, "tres").get_by_role("button", name="Borrar").click()
    page.get_by_role("dialog").get_by_role("button", name="Borrar").click()
    expect(row(page, "uno").get_by_role("button", name="Borrar")).to_be_focused()
