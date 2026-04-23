# question.market — contract bug bounty report

Target: `qmrkt/contracts`, testnet deployment
Reporter: sunrobert

Two in-scope contract-level bugs are described below. Both are reproducible against the current `main` of `qmrkt/contracts` and do not rely on any documented trust assumption (they bypass one).

---

## Bug 1 — Rogue `ProtocolConfig` impersonation lets a market creator redirect protocol fees to themselves

### Summary

`MarketFactory.create_market` and `QuestionMarket.create` both treat "the config app is legitimate if it publishes `mfi = <this factory id>`" as sufficient. Any Algorand account can deploy its own `ARC4Contract` with a single `uint64` global key `mfi` set to the official factory's app id. The factory and market then accept that contract as the protocol config, read every fee / bond / treasury parameter from it, and burn the attacker-chosen values into a brand-new `QuestionMarket`.

The most direct monetization: set `pt` (protocol treasury) to the attacker's own address. Every micro-USDC of `protocol_fee_bps` collected on trades in that market is drained to the attacker the next time anyone calls `withdraw_protocol_fees`.

### Impact

- **Permanent theft of protocol fees** on every trade in any market the attacker spawns via this path. Anyone (including the attacker) can later call the permissionless `withdraw_protocol_fees`, which unconditionally sends the accumulated balance to `protocol_treasury`.
- The same primitive also lets the attacker override every other governance-defined value on their own markets (`challenge_bond*`, `proposal_bond*`, `proposer_fee*`, `residual_linear_lambda_fp`, `min_challenge_window_secs`), so they can configure markets outside the protocol's intended economic bounds without asking the `ProtocolConfig.admin`.
- The frontend at https://question.market is expected to index all markets created by the official factory; a rogue market created via this path appears next to honest markets. Users who trade in it pay fees that never reach the real treasury.

This is "theft of funds"—the USDC collected from users for the protocol treasury is diverted—and it is *not* covered by the documented trust surface (the whitepaper trusts the resolver authority and LMSR math; it does not say "market creators may choose their own `ProtocolConfig`").

### Root cause

`smart_contracts/market_factory/contract.py`, `create_market`:

```python
protocol_config_id = Txn.applications(1).id
protocol_config_app = Application(protocol_config_id)
linked_factory_id = self._config_uint64(protocol_config_app, Bytes(b"mfi"))
assert linked_factory_id == Global.current_application_id.id
```

`smart_contracts/market_app/contract.py`, `QuestionMarket.create` (same pattern):

```python
config_app = Application(protocol_config)
...
linked_factory_id = self._config_uint64(config_app, Bytes(b"mfi"))
self._require(linked_factory_id > UInt64(0))
self._require(Global.creator_address == Application(linked_factory_id).address)
```

Both sides only verify that the supplied config app publishes `mfi = <official factory id>`. Because the rogue config is a standalone ARC4Contract the attacker deploys, the attacker is free to write any value it likes to the `mfi` global key. The check is effectively "does the config claim to trust this factory"—which any config can claim. Neither side compares `protocol_config_id` against a known-good id stored in the factory (or signed by an admin), and `ProtocolConfig.create` itself does not gate the initial `market_factory_id` value in any way.

All subsequent reads in `QuestionMarket.create` — `pt` (protocol treasury), `pfb` (protocol fee bps), `rlf`, `cb`, `pb`, `cbb`, `pbb`, `cbc`, `pbc`, `pfd`, `pff`, `mcw` — come straight out of whichever config app passed that single check, and are stored in market global state as the authoritative values for the rest of the market's life.

### Reproduction (testnet)

1. **Deploy a rogue `ProtocolConfig`** using the same ARC-56 schema as the real one (you can literally reuse `smart_contracts/artifacts/protocol_config`). In the `create` call, pass:
   - `market_factory_id = <official MarketFactory app id>`
   - `protocol_treasury = <attacker address>`
   - Everything else to values that pass the on-chain bounds checks in `ProtocolConfig.create` (`default_b > 0`, all bps keys ≤ 10_000, bonds satisfying `cap >= min`, etc.). None of these other values matter — only `pt` and `mfi` are load-bearing for the attack.
2. **Call `MarketFactory.create_market`** with `foreign_apps[1] = <rogue config id>`, `algo_funding >= CREATE_MARKET_MIN_FUNDING`, and a valid `usdc_funding`/`deposit_amount`. Every assertion passes — `linked_factory_id == Global.current_application_id.id` is satisfied because the rogue config set `mfi` to the real factory.
3. The factory does its normal inner-txn choreography: inner app-create, fund, `initialize`, `bootstrap`. The new `QuestionMarket`'s global state now has `protocol_treasury = <attacker>`, `protocol_fee_bps = <whatever the rogue config said>`, etc.
4. Users trade. On every `buy`/`sell`, `protocol_fee_balance` grows by `ceil(cost * protocol_fee_bps / 10_000)`.
5. **Anyone** calls `withdraw_protocol_fees()` (no sender auth). The USDC is `itxn.AssetTransfer`-ed to `Account(self.protocol_treasury.value)` — the attacker's address.

The Python model in `smart_contracts/market_app/active_lp_model.py` and the factory test harness in `tests/contracts/test_market_factory_integration.py` both confirm the same flow without any additional guard.

### Suggested fix

Either:
- Store the official `ProtocolConfig` app id as immutable factory state at factory-deploy time (or set it with a one-shot admin-only method), and have both `MarketFactory.create_market` and `QuestionMarket.create` compare the supplied config id against that stored id, OR
- Have `ProtocolConfig.create` require `market_factory_id == Global.caller_application_id` (or a similar cryptographic back-link the attacker cannot forge without owning the factory).

A pure "config publishes mfi" check is not sufficient because the config side of that link is written by whoever deployed the config.

---

## Bug 2 — Creator-supplied market parameters are never checked against the `ProtocolConfig` bounds that are supposed to govern them

### Summary

`ProtocolConfig` exposes governance bounds on several market parameters — `max_lp_fee_bps`, `min_grace_period_secs`, `max_outcomes`, `protocol_fee_ceiling_bps` — and `ProtocolConfig.update_*` methods enforce them for admin-driven changes. But `QuestionMarket.create` never reads these bound keys, so a market creator can pick any value they want for the creator-supplied args (`lp_fee_bps`, `grace_period_secs`, `num_outcomes`, `challenge_window_secs`, `cancellable`, `lp_entry_max_price_fp`).

Only two creator-supplied values are bounded at all:
- `challenge_window_secs >= mcw` (`min_challenge_window_secs`)
- `num_outcomes` against the hard-coded constants `MIN_OUTCOMES=2` / `MAX_OUTCOMES=8` (not against `max_outcomes` from config)
- `lp_entry_max_price_fp <= SCALE`

Everything else is accepted verbatim. In particular, `lp_fee_bps` has **no upper bound whatsoever** — not even against `10_000` — and `grace_period_secs` has **no lower bound** and no cap.

### Impact

- **`lp_fee_bps` can be set to any `uint64`.** Setting `lp_fee_bps = 10_000` makes every buy/sell pay an additional 100% in fees on top of the LMSR cost. Setting it higher (`lp_fee_bps > 10_000`) makes every sell revert, because `_calc_fee_up(gross_return, lp_fee_bps) + _calc_fee_up(gross_return, protocol_fee_bps) > gross_return` trips the `self._require(gross_return >= lp_fee + protocol_fee)` check. Any user who bought shares in a market where the creator later changes their mind about fees at deploy time (or who trades in a creator-rigged honeypot market) can be charged arbitrarily much on exit, or have their exit blocked entirely — the shares are still claimable at resolution, but *trading* them during `ACTIVE` is broken, which is explicitly listed in scope as "make a contract unusable for its core functionality: ... trading ...".
- **`grace_period_secs` can be set to any `uint64`.** Any non-authority user's ability to call `propose_resolution` is gated by `grace_expired = now >= deadline + grace_period_secs`. A creator who sets `grace_period_secs = 2**63` makes this expression either never true or overflow (unsigned addition panics in Algopy). If the `resolution_authority` never proposes (or is griefed by the creator into delaying), the market sits in `STATUS_RESOLUTION_PENDING` forever. Users cannot `claim`, cannot `refund` (no cancel path without creator consent and `cancellable=1`), and cannot `sell` (past deadline). **All user cost basis is permanently locked.**
- Both failure modes map cleanly to the "permanent lock" / "unusable for resolution" clauses of the bounty scope.

### Root cause

`smart_contracts/market_app/contract.py`, `QuestionMarket.create`:

```python
min_challenge_window_secs = self._config_uint64(config_app, Bytes(b"mcw"))
# ... no reads for max_lp_fee_bps, min_grace_period_secs, max_outcomes ...
self._require(challenge_window_secs.as_uint64() >= min_challenge_window_secs)
self._require(outcome_count >= UInt64(MIN_OUTCOMES))
self._require(outcome_count <= UInt64(MAX_OUTCOMES))
# ...
self.lp_fee_bps.value = lp_fee_bps.as_uint64()     # unchecked
self.grace_period_secs.value = grace_period_secs.as_uint64()   # unchecked
```

The `ProtocolConfig` keys `max_lp_fee_bps`, `min_grace_period_secs`, `max_outcomes`, and `protocol_fee_ceiling_bps` are set and admin-guarded on the config side but never consulted by the market contract. They read today as dead-weight global state from the market's point of view.

Note this bug is independent of Bug 1 — it applies even when the market points at the *real* `ProtocolConfig`.

### Reproduction

**Variant A — excessive LP fee:**
1. Call `MarketFactory.create_market(..., lp_fee_bps=arc4.UInt64(20_000), ...)` with the real `ProtocolConfig`.
2. After bootstrap, have any user `buy` shares. `cost` is computed correctly, but `lp_fee = ceil(cost * 20_000 / 10_000) = 2 * cost`; the buyer pays `~3 * cost` instead of `cost + normal_fee`.
3. That user attempts to `sell` any shares. `gross_return - lp_fee - protocol_fee` underflows the `_require(gross_return >= lp_fee + protocol_fee)` check with `lp_fee_bps = 20_000`, reverting every sell.

**Variant B — grace-period lock:**
1. Call `MarketFactory.create_market(..., grace_period_secs=arc4.UInt64((1 << 63)), cancellable=arc4.Bool(False), ...)`.
2. Users trade normally until `deadline`.
3. `trigger_resolution` succeeds; `status = STATUS_RESOLUTION_PENDING`.
4. Any non-authority caller hits `grace_expired = now >= deadline + 2**63` → either false forever or overflows and reverts.
5. If the `resolution_authority` never submits `propose_resolution` (or the creator bribes/colludes/waits them out), the market is wedged. No path back to `ACTIVE` (deadline passed), no cancel (creator set `cancellable=0`), no claim (no `STATUS_RESOLVED`), no refund (no `STATUS_CANCELLED`). Every `total_outstanding_cost_basis` worth of USDC a user bought into the pool is locked.

### Suggested fix

In `QuestionMarket.create`, read and enforce the following keys against the creator-supplied args:
- `lp_fee_bps <= max_lp_fee_bps`
- `grace_period_secs >= min_grace_period_secs` (and ideally `<= some_max_grace_period_secs`)
- `num_outcomes <= max_outcomes` (in addition to the hard-coded `MAX_OUTCOMES`)

Additionally, consider capping `grace_period_secs` by `deadline - now` or similar so `deadline + grace_period_secs` cannot overflow a `uint64`.

---

## Scope check

Both issues are contract-level. Bug 1 redirects USDC that is collected from end users for the protocol and loses them to an attacker — a theft under the "affect user funds" clause. Bug 2 (Variant B) permanently locks every user's cost basis in affected markets — a lock under the same clause, and also breaks both "trading" and "resolution", which the scope lists as core functionality. Neither bug depends on the documented resolver authority, blueprint oracle, or LMSR trust assumptions listed as out of scope.
