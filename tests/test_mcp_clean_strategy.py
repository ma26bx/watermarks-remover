"""MCP startup must load its packaged strategy independently of the cwd."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "service"))
sys.path.insert(0, str(ROOT / "service" / "scripts"))

import mcp_server


def test_packaged_strategy_from_another_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("WATERMARKS_CLEAN_STRATEGY_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mcp_server.core, "_DEFAULT_STRATEGY", None)
    mcp_server.configure_clean_strategy()
    assert mcp_server.core._DEFAULT_STRATEGY == "paraphrase@0.5"


def test_explicit_strategy_overrides_packaged_default(monkeypatch, tmp_path):
    config = tmp_path / "strategy.json"
    config.write_text('{"default_strategy": "structural@0.3"}')
    monkeypatch.setenv("WATERMARKS_CLEAN_STRATEGY_FILE", str(config))
    monkeypatch.setattr(mcp_server.core, "_DEFAULT_STRATEGY", None)
    mcp_server.configure_clean_strategy()
    assert mcp_server.core._DEFAULT_STRATEGY == "structural@0.3"


def test_invalid_explicit_strategy_is_not_silently_ignored(monkeypatch, tmp_path):
    config = tmp_path / "strategy.json"
    config.write_text('{"default_strategy": "invalid@0.3"}')
    monkeypatch.setenv("WATERMARKS_CLEAN_STRATEGY_FILE", str(config))
    with pytest.raises(ValueError, match="unknown strategy tactic"):
        mcp_server.configure_clean_strategy()
