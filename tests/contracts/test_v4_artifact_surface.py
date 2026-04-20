from __future__ import annotations

import json
from pathlib import Path

import smart_contracts.market_factory.contract as factory_module


ROOT_DIR = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = ROOT_DIR / "smart_contracts" / "artifacts"


def _arc56_methods(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [method["name"] for method in data.get("methods", [])]


def test_market_app_arc56_includes_active_lp_surface() -> None:
    methods = _arc56_methods(ARTIFACTS_DIR / "market_app" / "QuestionMarket.arc56.json")

    assert "bootstrap" in methods
    assert "enter_lp_active" in methods
    assert "claim_lp_fees" in methods
    assert "withdraw_lp_fees" in methods
    assert "claim_lp_residual" in methods
    assert "provide_liq" not in methods
    assert "withdraw_liq" not in methods


def test_market_factory_arc56_includes_canonical_creation_path() -> None:
    methods = _arc56_methods(ARTIFACTS_DIR / "market_factory" / "MarketFactory.arc56.json")

    assert "create_market" in methods


def test_protocol_config_arc56_includes_v4_guardrail_controls() -> None:
    methods = _arc56_methods(ARTIFACTS_DIR / "protocol_config" / "ProtocolConfig.arc56.json")

    assert "update_default_residual_linear_lambda_fp" in methods
    assert "update_max_active_lp_v4_outcomes" in methods


def test_generated_python_clients_expose_active_lp_methods() -> None:
    market_client = (ARTIFACTS_DIR / "market_app" / "market_app_client.py").read_text(encoding="utf-8")
    factory_client = (ARTIFACTS_DIR / "market_factory" / "market_factory_client.py").read_text(encoding="utf-8")
    protocol_client = (ARTIFACTS_DIR / "protocol_config" / "protocol_config_client.py").read_text(encoding="utf-8")

    assert "bootstrap" in market_client
    assert "enter_lp_active" in market_client
    assert "create_market" in factory_client
    assert "update_default_residual_linear_lambda_fp" in protocol_client
    assert "update_max_active_lp_v4_outcomes" in protocol_client


def test_market_factory_schema_constants_track_market_artifact() -> None:
    data = json.loads((ARTIFACTS_DIR / "market_app" / "QuestionMarket.arc56.json").read_text(encoding="utf-8"))
    schema = data["state"]["schema"]

    assert schema["global"]["ints"] == factory_module.QUESTION_MARKET_GLOBAL_UINTS
    assert schema["global"]["bytes"] == factory_module.QUESTION_MARKET_GLOBAL_BYTES
    assert schema["local"]["ints"] == factory_module.QUESTION_MARKET_LOCAL_UINTS
    assert schema["local"]["bytes"] == factory_module.QUESTION_MARKET_LOCAL_BYTES
