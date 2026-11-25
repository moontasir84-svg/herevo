"""The MCP server must give the same, sourced content as the app (it reads the same packs)."""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("mcp")
_path = Path(__file__).resolve().parents[2] / "mcp" / "herevo_mcp.py"
_spec = importlib.util.spec_from_file_location("herevo_mcp", _path)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_lists_countries():
    assert "BD" in m.list_countries()


def test_search_in_bangla_returns_sourced_entry():
    hits = m.search_knowledge("BD", "স্বামী মারা যাওয়ার পর দেবর বলছে জমি আমার না")
    assert hits[0]["id"] == "bd-rights-inheritance-widow" and hits[0]["source"] and hits[0]["review_status"] == "draft"


def test_farm_and_help():
    assert m.get_farm_advice("BD", "ধানের পাতা হলুদ")[0]["id"] == "bd-farm-rice-yellowing"
    assert any(c["phone"] == "16430" for c in m.find_help("BD", "legal_aid"))


def test_safety_check_is_shared_with_app():
    assert m.safety_check("দেবর হুমকি দিচ্ছে")["result"] == "ESCALATE"
    assert m.safety_check("স্বামী মারা গেছে, জমির ভাগ চাই")["result"] == "OK"


def test_unknown_country():
    with pytest.raises(ValueError):
        m.search_knowledge("ZZ", "land")
