#!/usr/bin/env python3
import os
import subprocess
from algopy import arc4
from algokit_utils.config import config
from dotenv import load_dotenv
from pathlib import Path

# Set up logging and load environment variables.
load_dotenv()
config.configure(debug=True, trace_all=False)

# Determine the root path based on this file's location.
root_path = Path(__file__).parent

# Compile the contracts
subprocess.run(["algob", "compile", "smart_contracts/market_factory/contract.py"], cwd=root_path)

# Create a new Algorand account
account = arc4.Account()

# Create a new QuestionMarket app
app_client = arc4.ARC4Client(
    "QuestionMarket",
    "smart_contracts/artifacts/market_app/market_app_client.py",
    account,
    app_id=None,
)

# Create a new market
app_client.create(
    creator=account.address,
    currency_asa=12345,
    num_outcomes=2,
)

# Get the app ID
app_id = app_client.app_id

# Create multiple users and have them buy from the market
for i in range(22):
    user_account = arc4.Account()
    user_app_client = arc4.ARC4Client(
        "QuestionMarket",
        "smart_contracts/artifacts/market_app/market_app_client.py",
        user_account,
        app_id=app_id,
    )
    try:
        user_app_client.buy(
            amount=100000,
            outcome=0,
        )
    except Exception as e:
        print(f"Error buying from market for user {i}: {e}")