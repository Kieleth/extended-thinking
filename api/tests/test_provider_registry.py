"""Tests for provider registry — get_provider(config) → MemoryProvider."""

import tempfile
from pathlib import Path

import pytest

from extended_thinking.providers import get_provider
from extended_thinking.providers.protocol import MemoryProvider


def test_get_provider_auto():
    provider = get_provider({"provider": "auto"})
    assert isinstance(provider, MemoryProvider)


def test_get_provider_folder():
    with tempfile.TemporaryDirectory() as tmp:
        provider = get_provider({"provider": "folder", "path": tmp})
        assert isinstance(provider, MemoryProvider)
        assert provider.name == "folder"


def test_get_provider_claude_code():
    with tempfile.TemporaryDirectory() as tmp:
        provider = get_provider({"provider": "claude-code", "path": tmp})
        assert isinstance(provider, MemoryProvider)
        assert provider.name == "claude-code"


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider({"provider": "nonexistent"})


def test_get_provider_default_is_auto():
    provider = get_provider({})
    assert isinstance(provider, MemoryProvider)
