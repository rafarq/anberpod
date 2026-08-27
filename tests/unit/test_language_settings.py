from __future__ import annotations

from pathlib import Path

from anberpod.app import Application
from anberpod.config import DataPaths
from anberpod.domain.models import InputAction, InputEvent
from anberpod.i18n import SUPPORTED_LANGUAGES
from anberpod.ui.state import LANGUAGE_CODES, Route, SettingsView


class Offline:
    def is_online(self) -> bool:
        return False


def _open(tmp_path: Path) -> Application:
    return Application.open(DataPaths.create(tmp_path / "data"), Offline())


def test_app_defaults_to_english_language_and_translates_home(tmp_path: Path) -> None:
    app = _open(tmp_path)
    assert app.language == "en"
    assert app.screen(Route.HOME).title == "Home"
    assert app.t("home_title") == "Home"


def test_set_language_normalizes_persists_and_falls_back_to_english(tmp_path: Path) -> None:
    app = _open(tmp_path)
    app.set_language("es_ES.UTF-8")
    assert app.language == "es"
    assert app.repositories.settings.get("language") == "es"

    app.set_language("not-a-real-language")
    assert app.language == "en"
    assert app.repositories.settings.get("language") == "en"


def test_language_persists_across_reopen(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    first = Application.open(DataPaths.create(data_dir), Offline())
    first.set_language("fr")

    second = Application.open(DataPaths.create(data_dir), Offline())
    assert second.language == "fr"
    assert second.screen(Route.HOME).title == "Accueil"


def test_settings_language_picker_navigation_and_save_flow(tmp_path: Path) -> None:
    app = _open(tmp_path)
    app.state.show(Route.SETTINGS)
    assert app.screen().items[1] == "Language"

    app.handle(InputEvent(InputAction.DOWN))  # focus "Language" row
    app.handle(InputEvent(InputAction.ACCEPT))  # open the language picker
    assert app.state.settings_view is SettingsView.LANGUAGE

    picker_screen = app.screen()
    assert picker_screen.items == tuple(SUPPORTED_LANGUAGES[code] for code in LANGUAGE_CODES)

    target_index = LANGUAGE_CODES.index("de")
    while app.state.focus != target_index:
        app.handle(InputEvent(InputAction.DOWN if app.state.focus < target_index else InputAction.UP))

    app.handle(InputEvent(InputAction.ACCEPT))  # save
    assert app.language == "de"
    assert app.state.settings_view is SettingsView.MENU
    assert app.repositories.settings.get("language") == "de"
    # Immediate next-frame redraw reflects the new language.
    assert app.screen(Route.HOME).title == "Start"


def test_settings_language_picker_back_returns_to_menu_without_saving(tmp_path: Path) -> None:
    app = _open(tmp_path)
    app.state.show(Route.SETTINGS)
    app.handle(InputEvent(InputAction.DOWN))
    app.handle(InputEvent(InputAction.ACCEPT))
    assert app.state.settings_view is SettingsView.LANGUAGE

    app.handle(InputEvent(InputAction.DOWN))
    app.handle(InputEvent(InputAction.BACK))

    assert app.state.settings_view is SettingsView.MENU
    assert app.state.route is Route.SETTINGS
    assert app.language == "en"
