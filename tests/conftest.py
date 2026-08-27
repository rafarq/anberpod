"""Shared pytest fixtures.

Tests that assert on literal UI strings (screen titles, status messages,
footers) must be locale-independent: they exercise ``Application`` without
ever selecting a language explicitly, so they implicitly rely on
:func:`anberpod.i18n.resolve_system_language` defaulting to English. Force a
neutral ``C`` locale for the whole test session so results don't flip
depending on the host shell's ``LANG``/``LC_ALL`` (mirrors how CI runners
default to ``C`` unless a test explicitly opts into another locale).
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _neutral_locale_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    # Explicit "en_US.UTF-8" (not bare "C"/"POSIX", which normalize_language
    # treats as unset and falls through to locale.getlocale() — a
    # process-level value fixed at interpreter startup that monkeypatching
    # the environment cannot change) so every test sees deterministic
    # English UI strings regardless of the host shell's LANG.
    monkeypatch.setenv("LANG", "en_US.UTF-8")
