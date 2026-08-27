from __future__ import annotations

import locale

import pytest

from anberpod.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    TRANSLATIONS,
    normalize_language,
    resolve_system_language,
    t,
)


def test_exactly_fifteen_supported_languages_with_native_names() -> None:
    assert len(SUPPORTED_LANGUAGES) == 15
    assert set(SUPPORTED_LANGUAGES) == set(TRANSLATIONS)
    assert all(isinstance(name, str) and name for name in SUPPORTED_LANGUAGES.values())
    assert SUPPORTED_LANGUAGES["en"] == "English"
    assert SUPPORTED_LANGUAGES["zh-Hans"] == "简体中文"


def test_every_language_has_the_same_key_set_as_english() -> None:
    english_keys = set(TRANSLATIONS[DEFAULT_LANGUAGE].keys())
    assert english_keys, "English bundle must not be empty"
    for language, bundle in TRANSLATIONS.items():
        assert set(bundle.keys()) == english_keys, f"{language} has a mismatched key set"


def test_every_translation_value_is_a_non_empty_string() -> None:
    for language, bundle in TRANSLATIONS.items():
        for key, value in bundle.items():
            assert isinstance(value, str) and value.strip(), f"{language}.{key} is empty"


@pytest.mark.parametrize("language", list(SUPPORTED_LANGUAGES))
def test_t_translates_every_language(language: str) -> None:
    assert t("home_title", language) == TRANSLATIONS[language]["home_title"]


def test_t_falls_back_to_english_for_unsupported_language() -> None:
    assert t("home_title", "xx") == TRANSLATIONS["en"]["home_title"]


def test_t_falls_back_to_raw_key_when_missing_everywhere() -> None:
    assert t("this_key_does_not_exist", "en") == "this_key_does_not_exist"


def test_t_interpolates_kwargs() -> None:
    assert t("settings_menu_version", "en", version="1.2.3") == "Version 1.2.3"
    assert t("settings_menu_version", "es", version="1.2.3") == "Versión 1.2.3"


def test_t_never_raises_on_bad_format_kwargs() -> None:
    # Missing/extraneous kwargs must not crash the on-device UI.
    assert t("home_title", "en", unexpected="value") == TRANSLATIONS["en"]["home_title"]


def test_normalize_language_accepts_bare_and_posix_locale_strings() -> None:
    assert normalize_language("es") == "es"
    assert normalize_language("es_ES") == "es"
    assert normalize_language("es-ES.UTF-8") == "es"
    assert normalize_language("pt_BR") == "pt"


def test_normalize_language_collapses_all_chinese_variants_to_simplified() -> None:
    for value in ("zh", "zh_CN", "zh_Hans", "zh_SG", "zh_TW", "zh_Hant", "zh-Hant-TW"):
        assert normalize_language(value) == "zh-Hans"


def test_normalize_language_rejects_unsupported_or_empty_values() -> None:
    assert normalize_language("xx_XX") is None
    assert normalize_language("") is None
    assert normalize_language(None) is None
    assert normalize_language("C") is None
    assert normalize_language("POSIX") is None


def test_resolve_system_language_reads_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert resolve_system_language() == "fr"


def test_resolve_system_language_prefers_lc_all_over_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    monkeypatch.setenv("LC_ALL", "ja_JP.UTF-8")
    assert resolve_system_language() == "ja"


def test_resolve_system_language_prefers_lc_messages_over_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    monkeypatch.setenv("LC_MESSAGES", "de_DE.UTF-8")
    assert resolve_system_language() == "de"


def test_resolve_system_language_falls_back_to_getlocale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.setattr(locale, "getlocale", lambda: ("ko_KR", "UTF-8"))
    assert resolve_system_language() == "ko"


def test_resolve_system_language_defaults_to_english_when_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "xx_XX.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setattr(locale, "getlocale", lambda: (None, None))
    assert resolve_system_language() == "en"
