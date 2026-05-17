"""Pydantic schemas for options analysis. Complete type system for Phase 2."""

from datetime import datetime, date as date_type
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


# ============================================================================
# Enums for structured choices
# ============================================================================

class SetupType(str, Enum):
    """Technical setup patterns detected by Setup Analyst."""
    ORB_BREAK = "orb_break"  # Opening range break
    VWAP_REJECT = "vwap_reject"  # VWAP reclaim/reject
    GAMMA_FLIP = "gamma_flip"  # Dealer gamma flip cross
    IV_CRUSH = "iv_crush"  # Post-earnings IV crush
    RV_IV_SPREAD = "rv_iv_spread"  # Realized vol << IV expansion
    MEAN_REVERT = "mean_revert"  # Reversion to mean
    VOLATILITY_PINCH = "volatility_pinch"  # Vol compression
    EMA_LEVEL = "ema_level"  # Bounce/reject at 8/21/50/200 EMA S/R (single or confluence)


class StrategyStructure(str, Enum):
    """Option strategy structures selected by Strategy Router."""
    VERTICAL_CALL_SPREAD = "vertical_call_spread"  # Bull call / bear call
    VERTICAL_PUT_SPREAD = "vertical_put_spread"  # Bull put / bear put
    CALENDAR_CALL = "calendar_call"
    CALENDAR_PUT = "calendar_put"
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    LONG_STRADDLE = "long_straddle"
    LONG_STRANGLE = "long_strangle"
    IRON_CONDOR = "iron_condor"
    LONG_GAMMA_SCALP = "long_gamma_scalp"
    SHORT_PREMIUM_FADE = "short_premium_fade"
    DIAGONAL_SPREAD = "diagonal_spread"


class OptionRight(str, Enum):
    """Call or Put."""
    CALL = "call"
    PUT = "put"


class LegAction(str, Enum):
    """Buy or Sell a leg."""
    BUY = "buy"
    SELL = "sell"


class Direction(str, Enum):
    """Directional bias."""
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"


class DebaterType(str, Enum):
    """The five debater archetypes."""
    DIRECTIONAL = "directional"    # Trend follower
    INCOME = "income"              # Theta collector
    VOLATILITY = "volatility"      # Vol expansion trader
    OPTIONS_FLOW = "options_flow"  # Smart money / institutional flow
    SENTIMENT = "sentiment"        # Crowd / news sentiment advocate


class DecisionStatus(str, Enum):
    """Status of a trade decision."""
    APPROVED = "approved"  # Passed all gates, ready to execute
    REJECTED = "rejected"  # Failed a gate (risk, liquidity, cost, etc)
    PENDING_APPROVAL = "pending_approval"  # Awaiting human review


class ExitReason(str, Enum):
    """Why a trade exited."""
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    TIME_STOP = "time_stop"
    HARD_EOD_CUTOFF = "hard_eod_cutoff"  # 3:55 PM ET force-close (no overnight holding)
    EXIT_RULE = "exit_rule"  # Technical exit (gamma flip, sentiment flip, etc)
    FORCED_CLOSE = "forced_close"  # Halt, margin call, circuit breaker, etc


# ============================================================================
# Core Decision Schemas
# ============================================================================

class VolRegime(BaseModel):
    """Volatility regime analysis from Vol Regime Analyst (Layer 0).

    IV cheap/expensive? Term structure shape? Skew extreme?
    """
    model_config = ConfigDict(extra='forbid')

    # IV metrics (0-1 scale: stored this way so debaters/PM can do *100 to display as %)
    iv_rank: float = Field(..., ge=0, le=1, description="IV percentile (0-1 scale, 0=cheap, 1=expensive)")
    iv_percentile: float = Field(..., ge=0, le=1, description="IV percentile rank (0-1)")

    # Term structure
    term_upward: bool = Field(..., description="Is term structure upward-sloping (contango)?")
    term_slope_pct: float = Field(..., description="Slope between front and back month, %")

    # Skew
    put_call_skew: float = Field(..., description="Put IV - Call IV (negative = skew down)")
    skew_extreme: bool = Field(..., description="Is skew at 95th+ percentile?")

    # Vol comparison
    realized_vol: float = Field(..., ge=0, description="20-day realized volatility %")
    rv_iv_ratio: float = Field(..., ge=0, description="RV / IV (>1 = expansion opportunity)")

    # Confidence
    confidence: float = Field(default=0.7, ge=0, le=1, description="Analyst confidence in regime read")
    regime_bias: Direction = Field(..., description="Regime implication: cheap favors long gamma, expensive short gamma")


class Setup(BaseModel):
    """Technical setup detected by Setup Analyst (Layer 2).

    Is a known pattern firing? ORB break, VWAP reject, etc.
    """
    model_config = ConfigDict(extra='forbid')

    setup_type: SetupType = Field(..., description="Pattern name")
    confidence: float = Field(..., ge=0, le=1, description="Confidence in the setup (0-1)")

    # Price levels
    entry_level: float = Field(..., gt=0, description="Suggested entry price")
    invalidation_level: float = Field(..., gt=0, description="Level where setup is invalidated")
    target_level: Optional[float] = Field(default=None, description="First profit target")

    # Technical context
    support_level: Optional[float] = Field(default=None, description="Nearest support")
    resistance_level: Optional[float] = Field(default=None, description="Nearest resistance")

    # Timeframe
    anchor_timeframe: str = Field(default="15m", description="Primary timeframe (1m, 5m, 15m, 1h, 1D)")
    multi_timeframe_alignment: float = Field(
        default=0,
        ge=-1,
        le=1,
        description="MTF alignment score (-1: against all TFs, 0: mixed, 1: aligned all TFs)"
    )

    # Reason
    rationale: str = Field(..., description="Why this setup is firing now")


class StrategyCandidate(BaseModel):
    """Candidate strategy structure with metadata."""
    model_config = ConfigDict(extra='forbid')

    structure: StrategyStructure = Field(..., description="Option structure type")
    win_rate_estimate: float = Field(..., ge=0, le=1, description="Historical win % from bandit")
    expected_return: float = Field(..., description="Expected return per trade (%, or ratio)")
    rationale: str = Field(..., description="Why this structure for this setup/regime")


class Strategy(BaseModel):
    """Strategy selection output from Strategy Selector (Layer 3).

    Which structure fits? Guided by bandit + cache (catalyst, sentiment).
    """
    model_config = ConfigDict(extra='forbid')

    selected_structure: StrategyStructure = Field(..., description="Primary chosen structure")
    candidates: List[StrategyCandidate] = Field(default_factory=list, description="Top 2-3 alternatives ranked")

    structure_rationale: str = Field(..., description="Why selected over alternatives")
    cache_modifiers: str = Field(
        default="",
        description="Cache-driven adjustments (e.g., 'earnings in 2 days → avoid short premium')"
    )


class OptionLeg(BaseModel):
    """A single leg of an option trade."""
    model_config = ConfigDict(extra='forbid')

    right: OptionRight = Field(..., description="Call or Put")
    strike: float = Field(..., gt=0, description="Strike price")
    expiration: date_type = Field(..., description="Expiration date")
    action: LegAction = Field(..., description="Buy or Sell")
    quantity: int = Field(..., gt=0, description="Number of contracts")


class ChainSnapshot(BaseModel):
    """Detailed chain analysis from Chain Builder (Layer 4).

    Strike selection, Greeks, cost, slippage estimate for a candidate leg structure.
    """
    model_config = ConfigDict(extra='forbid')

    # Legs
    legs: List[OptionLeg] = Field(..., min_items=1, description="The legs of the trade")

    # Cost
    debit_or_credit: float = Field(..., description="Total cost: positive = debit (cost us), negative = credit (we get paid)")
    cost_type: str = Field(..., description="'debit' or 'credit'")

    # Greeks (at entry)
    delta: float = Field(..., description="Portfolio delta (-1 to +1)")
    gamma: float = Field(..., description="Portfolio gamma")
    theta: float = Field(..., description="Portfolio theta (daily)")
    vega: float = Field(..., description="Portfolio vega (per 1% IV move)")

    # Cost analysis
    cost_vs_edge: str = Field(..., description="Edge vs costs: 'favorable', 'marginal', 'poor'")
    slippage_estimate_pct: float = Field(..., ge=0, description="Expected slippage as % of spread width")

    # Probability
    probability_to_target: float = Field(
        default=0.5, ge=0, le=1,
        description="Probability trade reaches target before stop (BSM or empirical)"
    )

    # Constraints
    liquidity_check: bool = Field(..., description="Is there sufficient liquidity to trade?")
    liquidity_note: str = Field(default="", description="Any liquidity concerns")


class GreeksSnapshot(BaseModel):
    """Portfolio Greeks after the proposed trade (Layer 5)."""
    model_config = ConfigDict(extra='forbid')

    # Current book + proposed trade
    net_delta: float = Field(..., description="Portfolio delta after trade")
    net_gamma: float = Field(..., description="Portfolio gamma after trade")
    net_theta: float = Field(..., description="Portfolio theta (daily) after trade")
    net_vega: float = Field(..., description="Portfolio vega after trade")

    # Risk metrics
    days_to_expiration: int = Field(..., ge=0, description="Days until nearest short expiration")
    vega_cap_utilization: float = Field(..., ge=0, le=1, description="% of daily vega cap used after trade")
    theta_cap_utilization: float = Field(..., ge=0, le=1, description="% of daily theta cap used after trade")

    # Correlation/clustering
    correlated_with_book: bool = Field(
        default=False, description="Is this trade highly correlated with existing positions?"
    )


class DebaterOpinion(BaseModel):
    """One debater's perspective on the trade (Layer 6.1)."""
    model_config = ConfigDict(extra='forbid')

    debater_type: DebaterType = Field(..., description="Which advocate: directional, income, or volatility")

    # Position
    direction: Direction = Field(..., description="Bullish, neutral, or bearish")
    conviction: float = Field(..., ge=0, le=1, description="Confidence in the view (0-1)")

    # Reasoning
    reasoning: str = Field(..., description="Why this debater favors this direction/structure")
    supporting_factors: List[str] = Field(..., min_items=1, description="Key factors supporting the opinion")
    counter_risks: List[str] = Field(default_factory=list, description="Risks or objections to the position")

    # Suggested action
    suggested_structure: Optional[StrategyStructure] = Field(
        default=None, description="If this debater's view were taken, what structure would they suggest?"
    )


class SentimentSnapshot(BaseModel):
    """Aggregated sentiment from multiple sources (Reddit, StockTwits, Alpaca News, yfinance).

    Output from sentiment aggregator. Input to Sentiment Debater (5th debater).
    """
    model_config = ConfigDict(extra='forbid')

    ticker: str = Field(..., description="Stock ticker")

    # Individual source scores (normalized -1 to +1)
    yfinance_score: float = Field(..., ge=-1, le=1, description="yfinance news sentiment (-1 to +1)")
    alpaca_score: float = Field(..., ge=-1, le=1, description="Alpaca news sentiment (-1 to +1)")
    reddit_bull_pct: float = Field(..., ge=0, le=1, description="Reddit bullish percentage (0-1)")
    stocktwits_bull_pct: float = Field(..., ge=0, le=1, description="StockTwits bullish percentage (0-1)")

    # Composite
    composite_score: float = Field(..., ge=-1, le=1, description="Weighted composite score (-1 to +1)")
    composite_label: str = Field(..., description="STRONGLY_BULLISH | BULLISH | NEUTRAL | BEARISH | STRONGLY_BEARISH")

    # Signal strength
    mention_count: int = Field(..., ge=0, description="Total mentions across Reddit + StockTwits")
    catalyst_tags: List[str] = Field(default_factory=list, description="Catalyst tags from news (earnings, m_and_a, fda, etc)")

    # Metadata
    data_sources: List[str] = Field(default_factory=list, description="Which sources returned data (yfinance, alpaca, reddit, stocktwits)")
    fetched_at: datetime = Field(..., description="When this snapshot was fetched")


class OptionsProposal(BaseModel):
    """Proposed trade before final risk gates (Layer 4)."""
    model_config = ConfigDict(extra='forbid')

    legs: List[OptionLeg] = Field(..., min_items=1, description="Legs of the trade")
    size: int = Field(..., gt=0, description="Number of contracts (applied to all legs)")

    # Directional bias
    direction: Direction = Field(..., description="Bullish, bearish, or neutral directional bias of the trade")

    # Entry/Exit
    entry_level: float = Field(..., gt=0, description="Proposed entry price")
    profit_target: float = Field(..., description="Profit target as $ or %")
    stop_loss: float = Field(..., description="Stop loss as $ or %")
    time_stop_days: Optional[int] = Field(default=None, ge=1, description="Exit after N days if P&L between targets")

    # Risk
    max_loss_dollars: float = Field(..., ge=0, description="Max loss in $")
    expected_pl_dollars: float = Field(..., description="Expected P&L in $")

    # Odds
    win_rate: float = Field(..., ge=0, le=1, description="Estimated win rate")
    expectancy: float = Field(..., description="Expected value per trade")


class MacroSignalLevel(str, Enum):
    """Severity of macro event signal."""
    CLEAR   = "clear"    # No event — normal trading
    CAUTION = "caution"  # Event within 60 min — reduce size
    HALT    = "halt"     # Breaking news or imminent event — freeze normal trades, crisis trades allowed


class MacroEventCategory(str, Enum):
    """Type of macro event — drives crisis trade structure selection."""
    NONE         = "none"           # No event
    GEOPOLITICAL = "geopolitical"   # War, terror, coup — strongly bearish
    ECONOMIC     = "economic"       # NFP, CPI, GDP — direction unknown until print
    FOMC         = "fomc"           # Fed decision — direction unknown, vol spike certain
    OIL_SHOCK    = "oil_shock"      # Oil crash/spike — sector-specific bearish
    FLASH_CRASH  = "flash_crash"    # Sudden market halt/circuit breaker
    BANK_CRISIS  = "bank_crisis"    # Bank failure, credit event — bearish


class MacroSignal(BaseModel):
    """Output of MacroEventAgent — read by Portfolio Manager and CrisisTradeAgent."""
    model_config = ConfigDict(extra='forbid')

    level: MacroSignalLevel = Field(..., description="CLEAR / CAUTION / HALT")
    event_category: MacroEventCategory = Field(default=MacroEventCategory.NONE, description="What kind of event")
    event_name: str = Field(default="", description="Name of the event (e.g. 'FOMC Decision')")
    minutes_to_event: Optional[int] = Field(default=None, description="Minutes until scheduled event")
    headlines: List[str] = Field(default_factory=list, description="Breaking news headlines detected")
    reasoning: str = Field(default="", description="Why this signal level was set")
    size_multiplier: float = Field(default=1.0, ge=0.0, le=1.0, description="Normal trade size scalar")
    crisis_bias: Direction = Field(default=Direction.NEUTRAL, description="Directional bias for crisis trades")
    crisis_trade_allowed: bool = Field(default=False, description="Whether crisis/hedge trades are permitted")


class CrisisCloseOrder(BaseModel):
    """Instruction to close an existing position during a crisis."""
    model_config = ConfigDict(extra='forbid')

    position_id: str = Field(..., description="ID of the position to close")
    ticker: str = Field(..., description="Ticker symbol")
    reason: str = Field(..., description="Why closing (e.g. 'crisis hedge — switching bearish')")
    urgency: str = Field(default="market_order", description="market_order | limit_at_bid")


class CrisisAction(BaseModel):
    """Full crisis response: close longs + enter crisis trade."""
    model_config = ConfigDict(extra='forbid')

    event_category: MacroEventCategory = Field(..., description="What triggered crisis mode")
    crisis_bias: Direction = Field(..., description="Directional read on the crisis")
    close_orders: List[CrisisCloseOrder] = Field(default_factory=list, description="Positions to close immediately")
    crisis_structure: StrategyStructure = Field(..., description="New crisis trade structure")
    crisis_size: int = Field(..., gt=0, description="Contracts — always small (max 3)")
    max_loss_dollars: float = Field(..., description="Max loss on crisis trade (defined-risk only)")
    reasoning: str = Field(..., description="Full rationale for crisis response")
    llm_analysis: str = Field(default="", description="LLM colour on the event and trade")


class OptionsDecision(BaseModel):
    """Final trade decision output by Portfolio Manager (Layer 5).

    This is what gets logged to the journal and executed (or rejected).
    """
    model_config = ConfigDict(extra='forbid')

    # Status
    status: DecisionStatus = Field(..., description="Approved, rejected, or pending")
    reject_reason: Optional[str] = Field(default=None, description="If rejected, why?")

    # Trade details
    ticker: str = Field(..., description="Stock ticker")
    decision_date: date_type = Field(..., description="Date of decision")
    decision_timestamp: datetime = Field(..., description="Timestamp of decision")

    # The actual trade
    proposal: Optional[OptionsProposal] = Field(default=None, description="The proposed trade (if approved)")

    # Greeks after trade
    portfolio_greeks: Optional[GreeksSnapshot] = Field(default=None, description="Portfolio Greeks after trade")

    # Reasoning trail (for learning)
    vol_regime: Optional[VolRegime] = Field(default=None, description="Vol analysis")
    setup: Optional[Setup] = Field(default=None, description="Technical setup")
    strategy: Optional[Strategy] = Field(default=None, description="Strategy selection")
    chain_snapshot: Optional[ChainSnapshot] = Field(default=None, description="Chain analysis")
    debater_opinions: List[DebaterOpinion] = Field(default_factory=list, description="The three debaters' takes")

    # PM justification
    risk_justification: str = Field(
        ..., description="Why this trade fits risk parameters (or why it's rejected)"
    )
    sizing_rationale: str = Field(
        default="", description="Why this size (vol targeting, Kelly, etc)"
    )

    # P&L targets
    target_profit_dollars: float = Field(..., description="Target profit in $")
    max_loss_dollars: float = Field(..., description="Max acceptable loss in $")

    # Trade ID for logging
    trade_id: Optional[str] = Field(default=None, description="Unique ID for this trade (generated on approval)")


class OptionsOutcome(BaseModel):
    """Trade outcome after close (for learning loop, Phase 4b)."""
    model_config = ConfigDict(extra='forbid')

    # Linking
    trade_id: str = Field(..., description="Links to OptionsDecision")

    # Fills
    entry_fill_price: float = Field(..., gt=0, description="Actual entry price")
    exit_fill_price: float = Field(..., gt=0, description="Actual exit price")

    # P&L
    realized_pnl_dollars: float = Field(..., description="Realized P&L in $")
    realized_pnl_pct: float = Field(..., description="Realized P&L %")

    # Trade path
    max_favorable_excursion: float = Field(..., description="MFE in $")
    max_adverse_excursion: float = Field(..., description="MAE in $")
    time_in_trade_minutes: int = Field(..., ge=0, description="Minutes from entry to exit")

    # Exit
    exit_reason: ExitReason = Field(..., description="Why trade exited")
    exit_timestamp: datetime = Field(..., description="When trade exited")

    # Model accuracy
    slippage_vs_model: float = Field(..., description="Actual slippage - predicted slippage")
    cost_model_accuracy: str = Field(..., description="'accurate', 'underestimated', 'overestimated'")

    # Lessons for learning
    lessons: List[str] = Field(default_factory=list, description="Lessons for RAG diary and future playbooks")
    surprising_result: bool = Field(default=False, description="Did this trade surprise?")


# ============================================================================
# Options Flow Monitoring (Layer 0.8)
# ============================================================================

class FlowSignalType(str, Enum):
    """Classification of institutional flow."""
    SWEEP = "sweep"           # Large OTM order hitting bid/ask
    HEDGE = "hedge"           # Apparent portfolio hedge (OTM puts bulk buying)
    ROLL = "roll"             # Closing one leg + opening another (roll-outs, roll-ups)
    GAMMA_BUY = "gamma_buy"   # ATM purchasing ahead of earnings/event
    BLOCK_TRADE = "block"     # Large single print, likely negotiated


class FlowSignal(BaseModel):
    """Options flow intelligence from sweep detection, P/C ratio shifts, dark pool prints.

    Output from Options Flow Monitor (Layer 0.8). Input to Setup Spotter + Strategy Router.
    Replaces blind sentiment_weight * flow_60m with actionable flow bias + intensity.
    """
    model_config = ConfigDict(extra='forbid')

    ticker: str = Field(..., description="Stock ticker")
    flow_timestamp: datetime = Field(..., description="When the flow was detected")

    # Flow characteristics
    signal_type: FlowSignalType = Field(..., description="Type of flow detected")
    bias: Direction = Field(..., description="Bullish, neutral, or bearish interpretation")
    intensity: float = Field(..., ge=0, le=1, description="Intensity of signal (0=weak, 1=strong)")

    # Confidence
    confidence: float = Field(..., ge=0, le=1, description="Confidence in interpretation (0-1)")

    # Context
    num_contracts: Optional[int] = Field(default=None, description="Contracts traded in flow")
    affected_strikes: List[float] = Field(default_factory=list, description="Strike prices involved")
    expiration: Optional[date_type] = Field(default=None, description="Expiration date of flow")

    # Reasoning
    rationale: str = Field(..., description="Why this flow is bullish/bearish")


# ============================================================================
# Per-Ticker Playbooks (Layer 1.5 — Guardrails Cache)
# ============================================================================

class TimeWindow(BaseModel):
    """Avoid window for a ticker (pre-earnings, FOMC, etc)."""
    model_config = ConfigDict(extra='forbid')

    start_hour: int = Field(..., ge=0, le=23, description="Start hour (0-23, 24h ET)")
    end_hour: int = Field(..., ge=0, le=23, description="End hour (0-23, 24h ET)")
    reason: str = Field(..., description="Why avoid this window (e.g., earnings, geopolitical risk)")
    active_through: Optional[date_type] = Field(default=None, description="Date when this window expires")


class PlaybookEntry(BaseModel):
    """Per-ticker guardrails and heuristics learned from historical trades.

    One PlaybookEntry per Tier 1 ticker. Encodes:
    - Position size caps (contracts, dollar notional)
    - Avoid windows (pre-earnings, FOMC, etc)
    - Favored structures (iron condor, calendar spreads, etc)
    - Unfavored structures or conditions
    - Greeks caps per ticker (max delta, max vega, etc)
    - Cost limits (max debit, min credit)
    """
    model_config = ConfigDict(extra='forbid')

    ticker: str = Field(..., description="Stock ticker (e.g. 'SPY', 'AAPL')")
    last_updated: datetime = Field(default_factory=datetime.utcnow, description="When playbook was last updated")

    # Position sizing
    max_contracts: int = Field(default=5, ge=1, description="Max contracts per trade")
    max_notional_dollars: float = Field(default=50000, gt=0, description="Max dollar exposure")

    # Greeks caps
    max_delta_per_leg: float = Field(default=0.8, ge=0, le=1, description="Max delta on any leg")
    max_vega_total: float = Field(default=500, description="Max total vega exposure")
    max_theta_target: float = Field(default=-20, description="Max negative theta per day")

    # Cost gates
    min_credit_collected: float = Field(default=0.10, ge=0, description="Min credit on short spreads (% of width)")
    max_debit_paid: float = Field(default=0.90, ge=0, description="Max debit on long spreads (% of width)")

    # Preferred structures (by win rate)
    favored_structures: List[StrategyStructure] = Field(
        default_factory=list, description="Structures with >55% win rate"
    )
    avoid_structures: List[StrategyStructure] = Field(
        default_factory=list, description="Structures with <40% win rate or repeated losses"
    )

    # Avoid windows (pre-earnings, FOMC, etc)
    avoid_windows: List[TimeWindow] = Field(
        default_factory=list, description="Times/dates when not to trade this ticker"
    )

    # Expiration preferences
    preferred_dte: List[int] = Field(
        default_factory=lambda: [21, 7], description="Preferred days-to-expiration (e.g. 21 or 7)"
    )
    avoid_0dte: bool = Field(default=False, description="Never trade 0DTE on this ticker?")

    # Directional bias (based on historical regime)
    typical_direction: Optional[Direction] = Field(
        default=None, description="Ticker's typical bias (SPY=bullish, gold=varies)"
    )

    # Notes
    notes: str = Field(default="", description="Qualitative notes on ticker behavior")


class PlaybookDiff(BaseModel):
    """Proposed change to a PlaybookEntry for A/B testing or rebalancing.

    Used when:
    - Win rate for a structure shifts significantly (e.g., 60% → 40%)
    - New avoid window is discovered (macro event)
    - Position caps need adjustment (liquidity, broker constraints)
    """
    model_config = ConfigDict(extra='forbid')

    ticker: str = Field(..., description="Ticker affected")
    change_type: str = Field(..., description="Type of change: 'add_structure', 'remove_structure', 'add_avoid_window', 'adjust_size_cap', 'update_greeks_cap'")

    # Current and proposed values
    current_value: Optional[str] = Field(default=None, description="Current value (JSON-serialized if complex)")
    proposed_value: Optional[str] = Field(default=None, description="Proposed new value")

    # Rationale
    reason: str = Field(..., description="Why this change is needed (e.g., 'last 10 trades on VERTICAL_CALL_SPREAD: 30% win rate')")
    win_rate_impact: Optional[float] = Field(default=None, description="Estimated impact on overall win rate (percentage points)")

    # A/B testing
    apply_immediately: bool = Field(default=False, description="Apply now (True) or A/B test vs current?")
    test_duration_trades: Optional[int] = Field(default=None, ge=5, description="Number of trades for A/B test")

    # Tracking
    proposed_date: datetime = Field(default_factory=datetime.utcnow, description="When this diff was proposed")
    approved_date: Optional[datetime] = Field(default=None, description="When/if approved by coach")
    applied_date: Optional[datetime] = Field(default=None, description="When applied (null if still in A/B test)")
