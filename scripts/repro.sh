#!/usr/bin/env bash
set -e

# Reproduction script for withdraw_dispute_sink issue
# This script sets up a local Ethereum testnet using Anvil (Foundry) and runs a simple deployment
# Adjust paths and parameters as needed for the project.

if ! command -v anvil >/dev/null 2>&1; then
  echo "Anvil (Foundry) not found. Install Foundry: curl -L https://foundry.paradigm.xyz | bash && source ~/.bashrc && foundryup"
  exit 1
fi

# Start local Anvil node
anvil -p 8545 &
ANVIL_PID=$!
trap "kill $ANVIL_PID" EXIT

# Wait a moment for node to be ready
sleep 2

# Deploy contracts (example command, adjust to actual deployment script)
# forge script script/Deploy.s.sol:Deploy --fork-url http://127.0.0.1:8545 --broadcast

echo "Replace the above command with the actual deployment script for this repository."
