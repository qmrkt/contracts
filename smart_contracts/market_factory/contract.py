from algopy import arc4
from algopy import types as t

# Define the market app
class QuestionMarket(arc4.App):
    @arc4.create
    def create(self, creator: t.Address, currency_asa: t.UInt64, num_outcomes: t.UInt64):
        # Create a new market
        market = arc4.Box(
            name="market",
            value_type=t.Bytes,
            default_value=b"{}",
        )
        self.local_state[market] = market

    @arc4.method
    def buy(self, amount: t.UInt64, outcome: t.UInt64):
        # Buy from the market
        market = self.local_state["market"]
        market_value = market.value
        if market_value is None:
            market_value = b"{}"
        market_value = eval(market_value)
        market_value[outcome] = market_value.get(outcome, 0) + amount
        market.value = str(market_value).encode()