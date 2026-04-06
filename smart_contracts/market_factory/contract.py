from algopy import ARC4Contract, BoxMap, Bytes, Global, GlobalState, UInt64, arc4, op, subroutine

from smart_contracts.market_app.contract import QuestionMarket
from smart_contracts.protocol_config.contract import (
    KEY_CHALLENGE_BOND,
    KEY_DEFAULT_B,
    KEY_MAX_LP_FEE_BPS,
    KEY_MAX_OUTCOMES,
    KEY_MIN_BOOTSTRAP_DEPOSIT,
    KEY_MIN_CHALLENGE_WINDOW_SECS,
    KEY_MIN_GRACE_PERIOD_SECS,
    KEY_PROPOSAL_BOND,
    KEY_PROTOCOL_FEE_BPS,
)

MIN_OUTCOMES = 2
MARKET_REGISTRY_PREFIX = b"m"


class MarketFactory(ARC4Contract):
    """Factory/registry for market app deployment."""

    def __init__(self) -> None:
        self.protocol_config_id = GlobalState(UInt64, key="pcid")
        self.resolution_authority = GlobalState(Bytes, key="ra")

        self.market_registry = BoxMap(UInt64, Bytes, key_prefix=MARKET_REGISTRY_PREFIX)

    def _require(self, condition: bool) -> None:
        assert condition

    @arc4.baremethod()
    def bare_noop(self) -> None:
        pass

    @subroutine(inline=False)
    def _protocol_config_uint64(self, key: Bytes) -> UInt64:
        value, exists = op.AppGlobal.get_ex_uint64(self.protocol_config_id.value, key)
        self._require(exists)
        return value

    def _create_market_app_inner_transaction(
        self,
        creator: arc4.Address,
        currency_asa: arc4.UInt64,
        num_outcomes: arc4.UInt64,
        initial_b: arc4.UInt64,
        lp_fee_bps: arc4.UInt64,
        deadline: arc4.UInt64,
        question_hash: arc4.DynamicBytes,
        main_blueprint_hash: arc4.DynamicBytes,
        dispute_blueprint_hash: arc4.DynamicBytes,
        challenge_window_secs: arc4.UInt64,
        market_admin: arc4.Address,
        proposal_bond: arc4.UInt64,
        grace_period_secs: arc4.UInt64,
        cancellable: arc4.Bool,
    ) -> UInt64:
        created_market = arc4.arc4_create(
            QuestionMarket.create,
            creator,
            currency_asa,
            num_outcomes,
            initial_b,
            lp_fee_bps,
            arc4.UInt64(self._protocol_config_uint64(Bytes(KEY_PROTOCOL_FEE_BPS))),
            deadline,
            question_hash,
            main_blueprint_hash,
            dispute_blueprint_hash,
            challenge_window_secs,
            arc4.Address(self.resolution_authority.value),
            arc4.UInt64(self._protocol_config_uint64(Bytes(KEY_CHALLENGE_BOND))),
            proposal_bond,
            grace_period_secs,
            market_admin,
            arc4.UInt64(self.protocol_config_id.value),
            arc4.UInt64(Global.current_application_id.id),
            cancellable,
        )
        return created_market.created_app.id

    @arc4.abimethod(create="require")
    def create(
        self,
        protocol_config_id: arc4.UInt64,
        resolution_authority: arc4.Address,
    ) -> None:
        self._require(protocol_config_id.as_uint64() > UInt64(0))
        self._require(bool(resolution_authority))

        self.protocol_config_id.value = protocol_config_id.as_uint64()
        self.resolution_authority.value = resolution_authority.bytes

    @arc4.abimethod()
    def create_market(
        self,
        creator: arc4.Address,
        currency_asa: arc4.UInt64,
        question_hash: arc4.DynamicBytes,
        num_outcomes: arc4.UInt64,
        initial_liquidity_b: arc4.UInt64,
        lp_fee_bps: arc4.UInt64,
        main_blueprint_hash: arc4.DynamicBytes,
        dispute_blueprint_hash: arc4.DynamicBytes,
        deadline: arc4.UInt64,
        challenge_window_secs: arc4.UInt64,
        market_admin: arc4.Address,
        proposal_bond: arc4.UInt64,
        grace_period_secs: arc4.UInt64,
        cancellable: arc4.Bool,
        bootstrap_deposit: arc4.UInt64,
    ) -> None:
        min_grace_period_secs = self._protocol_config_uint64(Bytes(KEY_MIN_GRACE_PERIOD_SECS))
        num_outcomes_val = num_outcomes.as_uint64()
        bootstrap_amount = bootstrap_deposit.as_uint64()

        self._require(num_outcomes_val >= UInt64(MIN_OUTCOMES))
        self._require(num_outcomes_val <= self._protocol_config_uint64(Bytes(KEY_MAX_OUTCOMES)))
        self._require(lp_fee_bps.as_uint64() <= self._protocol_config_uint64(Bytes(KEY_MAX_LP_FEE_BPS)))
        self._require(
            challenge_window_secs.as_uint64()
            >= self._protocol_config_uint64(Bytes(KEY_MIN_CHALLENGE_WINDOW_SECS))
        )
        self._require(bootstrap_amount >= self._protocol_config_uint64(Bytes(KEY_MIN_BOOTSTRAP_DEPOSIT)))

        initial_b = initial_liquidity_b.as_uint64()
        if initial_b == UInt64(0):
            default_b = self._protocol_config_uint64(Bytes(KEY_DEFAULT_B))
            safe_default_b = bootstrap_amount
            if num_outcomes_val > UInt64(2):
                safe_default_b = bootstrap_amount // UInt64(2)
            if num_outcomes_val > UInt64(7):
                safe_default_b = bootstrap_amount // UInt64(3)
            if default_b <= safe_default_b:
                initial_b = default_b
            else:
                initial_b = safe_default_b
        resolved_proposal_bond = proposal_bond.as_uint64()
        if resolved_proposal_bond == UInt64(0):
            resolved_proposal_bond = self._protocol_config_uint64(Bytes(KEY_PROPOSAL_BOND))
        resolved_grace_period_secs = grace_period_secs.as_uint64()
        if resolved_grace_period_secs == UInt64(0):
            resolved_grace_period_secs = min_grace_period_secs
        self._require(initial_b > UInt64(0))
        self._require(initial_b <= bootstrap_amount)
        self._require(resolved_grace_period_secs >= min_grace_period_secs)

        created_app_id = self._create_market_app_inner_transaction(
            creator,
            currency_asa,
            num_outcomes,
            arc4.UInt64(initial_b),
            lp_fee_bps,
            deadline,
            question_hash,
            main_blueprint_hash,
            dispute_blueprint_hash,
            challenge_window_secs,
            market_admin,
            arc4.UInt64(resolved_proposal_bond),
            arc4.UInt64(resolved_grace_period_secs),
            cancellable,
        )
        self.market_registry[created_app_id] = creator.bytes
