# question.market contracts

Open-source Algorand smart contracts powering [question.market](https://question.market) -- a prediction market protocol using Logarithmic Market Scoring Rule (LMSR) pricing.

## Contracts

| Contract | Description |
|---|---|
| **QuestionMarket** | Per-market application: LMSR trading, resolution, disputes, claims |
| **MarketFactory** | Deploys and indexes QuestionMarket instances |
| **ProtocolConfig** | Protocol-wide governance parameters and fee configuration |

Written in [Algorand Python](https://github.com/algorandfoundation/puya) (Algopy), compiled to AVM TEAL bytecode. All contracts include ARC-56 specifications and generated typed clients.

## Quick start

```bash
# Prerequisites: Python 3.12+, AlgoKit CLI 2.0+, Docker (for localnet)

# Install dependencies
poetry install

# Start local Algorand network
algokit localnet start

# Build all contracts
algokit project run build

# Run tests
poetry run pytest tests/ -v

# Deploy to localnet
algokit project deploy localnet
```

## Structure

```
smart_contracts/
  market_app/        # QuestionMarket contract
  market_factory/    # MarketFactory contract
  protocol_config/   # ProtocolConfig contract
  lmsr_math.py       # Pure Python LMSR math
  lmsr_math_avm.py   # AVM-compatible LMSR (fixed-point)
  abi_types.py       # Shared ABI type definitions
  artifacts/         # Compiled TEAL, ARC-56 JSON, typed clients
tests/               # 30+ test files: lifecycle, adversarial, LMSR, disputes
tla/                 # TLA+ formal specifications and model checker configs
```

## Launch gate

The canonical contracts launch gate lives in [scripts/contracts-launch-gate.sh](scripts/contracts-launch-gate.sh).

It rebuilds the three shipped contracts and runs the launch-critical suites, including:

- LMSR math and no-free-money/property coverage
- protocol/factory and market runtime regressions
- adversarial launch/payment coverage
- artifact surface checks

Run it from the repo root with:

```bash
bash ./scripts/contracts-launch-gate.sh
```

## LMSR pricing

The protocol uses a Logarithmic Market Scoring Rule for automated market making. Two implementations exist:

- `lmsr_math.py` -- pure Python, used for off-chain quote calculation and tests
- `lmsr_math_avm.py` -- fixed-point arithmetic targeting the AVM's uint64/biguint constraints

## License

See [LICENSE](./LICENSE).
