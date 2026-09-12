import logging
import math
from typing import Dict, Any

logger = logging.getLogger("WaterfallHunter.PositionCalculator")

class PositionCalculator:
    def __init__(self, taker_fee_pct=0.06, slippage_pct=None, funding_pct=0.01, target_buffer_pct=0.05, target_rr=2.0, default_capital_usdt=50.0):
        self.fee_pct = taker_fee_pct
        self.slippage_pct = slippage_pct
        self.funding_pct = funding_pct
        self.target_buffer_pct = target_buffer_pct
        self.target_rr = target_rr
        self.default_capital = default_capital_usdt
        self.fallback_min_notional = 5.0

    def avoid_round_level(self, price: float, is_tp: bool) -> float:
        """Move a level away from a nearby magnitude-round price."""
        if not math.isfinite(price) or price <= 0:
            return price
        round_step = 10 ** math.floor(math.log10(price))
        nearest = round(price / round_step) * round_step
        if abs(price - nearest) <= round_step * 0.00025:
            buffer = max(price * 0.0002, round_step * 0.00005)
            return price - buffer if is_tp else price + buffer
        return price

    def align_to_tick(self, value: float, tick_size: float) -> float:
        """تراز کردن دقیق با Tick Size صرافی"""
        if not tick_size or tick_size <= 0:
            return round(value, 6)
        return float(format(round(value / tick_size) * tick_size, '.8f'))

    def align_to_step(self, amount: float, step_size: float) -> float:
        """تراز کردن دقیق با Step Size (Lot Size) قرارداد"""
        if not step_size or step_size <= 0:
            return round(amount, 2)
        return float(format(math.floor(amount / step_size) * step_size, '.8f'))

    @staticmethod
    def _precision_to_increment(precision: Any) -> float:
        """Use CCXT's market precision as an increment in TICK_SIZE mode.

        The live gateways use CCXT's default TICK_SIZE precision mode.  Inferring
        decimal places here would turn an actual one-unit tick into 0.1, so a
        missing or non-positive increment fails the setup instead of guessing.
        """
        if precision is None:
            return 0.0
        value = float(precision)
        if value <= 0:
            return 0.0
        return value

    def calculate_short_position(self, vwap_entry: float, recent_high: float = None, market_info: dict = None,
                                 mark_price: float = None,
                                 entry_slippage_pct: float | None = None,
                                 exit_slippage_pct: float | None = None) -> Dict[str, Any]:
        """
        محاسبه پوزیشن شرت (Short) با اعمال Fee و Slippage دوطرفه (ورود و خروج).
        فرضِ Stop-first: محاسبه ریسک بر اساس ضربه به استاپلاس.
        """
        if (
            not isinstance(vwap_entry, (int, float)) or isinstance(vwap_entry, bool)
            or not math.isfinite(vwap_entry) or vwap_entry <= 0
            or not isinstance(mark_price, (int, float)) or isinstance(mark_price, bool)
            or not math.isfinite(mark_price) or mark_price <= 0
        ):
            return {"status": "REJECTED: Invalid entry price"}

        entry_slippage = entry_slippage_pct if entry_slippage_pct is not None else self.slippage_pct
        exit_slippage = exit_slippage_pct if exit_slippage_pct is not None else self.slippage_pct
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0
            for value in (entry_slippage, exit_slippage)
        ):
            return {"status": "REJECTED: Missing measured slippage"}

        # --- 1. محاسبات محافظه‌کارانه ریسک و سطوح ---
        # محاسبه قیمت ورود واقعی (با احتساب اسلیپیج ورود و کارمزد تیکر)
        real_entry_cost = vwap_entry * (1 - (entry_slippage / 100))
        entry_fee_impact = real_entry_cost * (self.fee_pct/100)
        net_entry_price = real_entry_cost - entry_fee_impact # برای شورت، این بدتر می‌شود

        # محاسبه استاپلاس (بر اساس سقف قبلی یا ۲ درصد پیش‌فرض)
        if not recent_high or recent_high <= net_entry_price:
            base_sl = net_entry_price * 1.02
        else:
            base_sl = recent_high * 1.002

        sl_price = self.avoid_round_level(base_sl, is_tp=False)

        # محاسبه ریسک واقعی (با فرض اسلیپیج و کارمزد در لحظه استاپ‌خوردن)
        exit_sl_slippage = sl_price * (1 + (exit_slippage / 100))
        exit_sl_fee = exit_sl_slippage * (self.fee_pct/100)
        net_sl_cost = exit_sl_slippage + exit_sl_fee

        risk_per_coin = net_sl_cost - net_entry_price
        if risk_per_coin <= 0:
            return {"status": "REJECTED: Invalid risk math (SL <= Entry)"}

        risk_pct = (risk_per_coin / net_entry_price) * 100

        # محاسبه هدف اول و دوم (با در نظر گرفتن Fee و Slippage خروج با لیمیت/میکر)
        # هدف باید طوری باشد که بعد از کارمزدها سود خالص R:R را بدهد
        maker_fee_pct = 0.02 # پیش‌فرض محافظه‌کارانه میکر
        target_net_profit_1 = risk_per_coin * 1.0 # R:R 1:1
        target_net_profit_2 = risk_per_coin * self.target_rr # R:R 1:2

        # P_tp = Entry - Profit - Exit_Fee
        carrying_cost = net_entry_price * (
            (maker_fee_pct + self.funding_pct + self.target_buffer_pct + exit_slippage) / 100
        )
        raw_tp1 = net_entry_price - target_net_profit_1 - carrying_cost
        raw_tp2 = net_entry_price - target_net_profit_2 - carrying_cost

        # A short setup requires 0 < tp2 < tp1 < entry. Wide-stop or
        # high-cost configurations (small-priced meme contracts) can push a
        # target below zero or above entry; such geometry is invalid and must
        # fail the setup instead of persisting corrupted levels.
        if not (0 < raw_tp2 < raw_tp1 < net_entry_price):
            return {"status": "REJECTED: Invalid take-profit geometry"}

        tp1_price = self.avoid_round_level(raw_tp1, is_tp=True)
        tp2_price = self.avoid_round_level(raw_tp2, is_tp=True)

        # --- 2. اعمال محدودیت‌های صرافی (Contract, Tick, Step) ---
        tick_size, step_size, contract_size, min_notional = 0.0, 0.0, 1.0, self.fallback_min_notional

        if market_info:
            precision = market_info.get('precision', {})
            tick_size = self._precision_to_increment(precision.get('price'))
            step_size = self._precision_to_increment(precision.get('amount'))
            contract_size = float(market_info.get('contractSize', 1.0) or 1.0)

            limits = market_info.get('limits', {})
            min_notional = float(limits.get('cost', {}).get('min', self.fallback_min_notional) or self.fallback_min_notional)

        # تراز کردن سطوح
        entry_aligned = self.align_to_tick(net_entry_price, tick_size)
        sl_aligned = self.align_to_tick(sl_price, tick_size)
        tp1_aligned = self.align_to_tick(tp1_price, tick_size)
        tp2_aligned = self.align_to_tick(tp2_price, tick_size)
        # A coarse tick can collapse the aligned targets into each other,
        # past the entry, or round the stop onto the entry even when the raw
        # levels and risk math were valid.
        if not (0 < tp2_aligned < tp1_aligned < entry_aligned < sl_aligned):
            return {"status": "REJECTED: Invalid aligned take-profit geometry"}
        # محاسبه حجم پوزیشن (ارزش دلاری تقسیم بر قیمت ورود، سپس تقسیم بر سایز قرارداد)
        raw_amount_contracts = (self.default_capital / entry_aligned) / contract_size
        amount_aligned = self.align_to_step(raw_amount_contracts, step_size)

        actual_notional = amount_aligned * contract_size * entry_aligned
        is_executable = actual_notional >= min_notional and amount_aligned > 0

        return {
            "entry_price": entry_aligned,
            "stop_loss": sl_aligned,
            "take_profit_1": tp1_aligned,
            "take_profit_2": tp2_aligned,
            "position_size_contracts": amount_aligned,
            "position_value_usdt": round(actual_notional, 2),
            "is_api_ready": is_executable,
            "risk_pct": round(risk_pct, 2),
            "reward_to_risk": self.target_rr,
            "monitoring": {"take_profit_price_source": "best_ask", "stop_loss_price_source": "mark_price", "mark_price": mark_price},
            "slippage": {
                "entry_pct": round(entry_slippage, 6),
                "exit_pct": round(exit_slippage, 6),
                "round_trip_pct": round(entry_slippage + exit_slippage, 6),
                "source": "live_orderbook_vwap",
            },
            "status": "READY" if is_executable else f"REJECTED: Minimum notional requirement failed ({min_notional} USDT)"
        }
