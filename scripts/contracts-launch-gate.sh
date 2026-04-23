#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT_DIR}"

echo "==> Building contracts"
poetry run python -m smart_contracts build protocol_config
poetry run python -m smart_contracts build market_app
poetry run python -m smart_contracts build market_factory

echo "==> Running contracts launch gate"
poetry run pytest -q \
  tests/test_hyp_lmsr_math.py \
  tests/test_hyp_bond_settlement.py \
  tests/test_hyp_market_lifecycle.py \
  tests/test_c4_property_invariants.py \
  tests/test_lmsr_math.py \
  tests/test_lmsr_properties.py \
  tests/test_c4_payment_verification.py \
  tests/test_c6_launch_adversarial.py \
  tests/test_c6_redteam_extended.py \
  tests/test_mbr_topup_and_delete_on_zero.py \
  tests/contracts/test_protocol_config_factory.py \
  tests/contracts/test_market_factory_integration.py \
  tests/test_market_app_contract_runtime.py \
  tests/test_market_app_contract_v4_runtime.py \
  tests/contracts/test_v4_artifact_surface.py
