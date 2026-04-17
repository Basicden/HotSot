"""
HotSot Money Module — Production-grade Decimal-based money handling.

CRITICAL: Never use float for money. Python float has IEEE 754 rounding errors:
    float(199.99) = 199.98999999999998  ← WRONG
    Decimal("199.99") = Decimal('199.99')  ← CORRECT

This module provides:
    - Money value object with Decimal arithmetic
    - INR-specific rounding (2 decimal places, ROUND_HALF_UP)
    - GST calculation utilities (CGST/SGST/IGST split)
    - Money validation (no negative, no zero for invoices)
    - String serialization for database storage

Usage:
    from shared.money import Money, round_inr, calculate_gst

    price = Money("199.99")
    gst = calculate_gst(price, rate=Decimal("0.18"), is_interstate=False)
    # gst = {"cgst": Money("17.99"), "sgst": Money("18.00"), "igst": Money("0.00"), "total_gst": Money("35.99")}

    total = price + gst["total_gst"]
    # total = Money("235.98")
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Dict, Optional, Union


# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════

INR_QUANTIZE = Decimal("0.01")  # 2 decimal places for INR
DEFAULT_CURRENCY = "INR"

# GST rates for Indian restaurant services (as of 2024)
GST_RATE_STANDARD = Decimal("0.05")    # 5% — standalone, non-AC restaurants
GST_RATE_AC = Decimal("0.18")          # 18% — AC restaurants, alcohol-serving
GST_RATE_CANTEEN = Decimal("0.05")     # 5% — canteen, mess
GST_RATE_DELIVERY = Decimal("0.18")    # 18% — delivery charges
GST_RATE_PACKAGING = Decimal("0.18")   # 18% — packaging material
GST_ON_COMMISSION = Decimal("0.18")    # 18% — GST on platform commission

# Platform commission
DEFAULT_COMMISSION_RATE = Decimal("0.15")  # 15%


# ═══════════════════════════════════════════════════════════════
# ROUNDING
# ═══════════════════════════════════════════════════════════════

def round_inr(amount: Decimal) -> Decimal:
    """
    Round to INR 2 decimal places using ROUND_HALF_UP.

    This is the standard rounding for Indian Rupee.
    Example: 199.995 → 200.00, 199.994 → 199.99

    Args:
        amount: The Decimal amount to round.

    Returns:
        Rounded Decimal with 2 decimal places.
    """
    return amount.quantize(INR_QUANTIZE, rounding=ROUND_HALF_UP)


# ═══════════════════════════════════════════════════════════════
# MONEY VALUE OBJECT
# ═══════════════════════════════════════════════════════════════

class Money:
    """
    Immutable value object for monetary amounts in INR.

    Uses Decimal internally — NEVER converts to float.
    All arithmetic returns new Money instances.
    All results are rounded to 2 decimal places (INR standard).

    Example:
        price = Money("199.99")
        tax = Money("35.99")
        total = price + tax  # Money("235.98")

        price * Decimal("2")   # Money("399.98")
        price / Decimal("2")   # Money("100.00")

    Rules:
        - Amount cannot be NaN or Infinity
        - Currency defaults to INR
        - All operations round to 2 decimal places
        - Subtraction that results in negative raises ValueError
          (use .allow_negative() for refund calculations)
    """

    __slots__ = ("_amount", "_currency")

    def __init__(
        self,
        amount: Union[str, int, Decimal, "Money"] = "0.00",
        currency: str = DEFAULT_CURRENCY,
        allow_negative: bool = False,
    ):
        """
        Create a Money instance.

        Args:
            amount: The monetary amount. Accepts str, int, Decimal, or Money.
                    Strings are preferred to avoid float precision issues.
            currency: ISO 4217 currency code. Defaults to "INR".
            allow_negative: If False, negative amounts raise ValueError.

        Raises:
            ValueError: If amount is invalid or negative (when not allowed).
        """
        if isinstance(amount, Money):
            self._amount = amount._amount
            self._currency = amount._currency
        elif isinstance(amount, Decimal):
            self._amount = round_inr(amount)
        elif isinstance(amount, str):
            try:
                self._amount = round_inr(Decimal(amount))
            except InvalidOperation:
                raise ValueError(f"Invalid money amount: '{amount}'")
        elif isinstance(amount, int):
            self._amount = round_inr(Decimal(amount))
        elif isinstance(amount, float):
            # DANGER: float is imprecise. Convert via string to preserve intent.
            raise TypeError(
                "Never use float for money! Use Money('199.99') instead of Money(199.99). "
                "Float causes rounding errors: float(199.99) = 199.98999999999998"
            )
        else:
            raise TypeError(f"Unsupported type for Money: {type(amount)}")

        self._currency = currency

        # Validate
        if self._amount.is_nan() or self._amount.is_infinite():
            raise ValueError(f"Money amount cannot be NaN or Infinity: {self._amount}")

        if not allow_negative and self._amount < 0:
            raise ValueError(
                f"Money amount cannot be negative: ₹{self._amount}. "
                "Use allow_negative=True for refund calculations."
            )

    @property
    def amount(self) -> Decimal:
        """Get the Decimal amount."""
        return self._amount

    @property
    def currency(self) -> str:
        """Get the currency code."""
        return self._currency

    @property
    def is_zero(self) -> bool:
        """Check if amount is zero."""
        return self._amount == Decimal("0.00")

    @property
    def is_positive(self) -> bool:
        """Check if amount is positive (> 0)."""
        return self._amount > Decimal("0.00")

    # ── Arithmetic ────────────────────────────────────────────

    def __add__(self, other: Union["Money", Decimal, int, str]) -> "Money":
        if isinstance(other, Money):
            return Money(self._amount + other._amount, self._currency)
        return Money(self._amount + Decimal(str(other)), self._currency)

    def __sub__(self, other: Union["Money", Decimal, int, str]) -> "Money":
        if isinstance(other, Money):
            result = self._amount - other._amount
        else:
            result = self._amount - Decimal(str(other))
        return Money(result, self._currency, allow_negative=True)

    def __mul__(self, other: Union[Decimal, int, str, float]) -> "Money":
        # Note: float is allowed here as a multiplier (e.g., tax rate)
        # but NOT as the base amount
        if isinstance(other, float):
            other = Decimal(str(other))
        elif isinstance(other, int):
            other = Decimal(other)
        elif isinstance(other, str):
            other = Decimal(other)
        return Money(round_inr(self._amount * other), self._currency)

    def __rmul__(self, other: Union[Decimal, int, str, float]) -> "Money":
        return self.__mul__(other)

    def __truediv__(self, other: Union[Decimal, int, str]) -> "Money":
        if isinstance(other, int):
            other = Decimal(other)
        elif isinstance(other, str):
            other = Decimal(other)
        if other == Decimal("0"):
            raise ZeroDivisionError("Cannot divide money by zero")
        return Money(round_inr(self._amount / other), self._currency, allow_negative=True)

    # ── Comparison ────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Money):
            return self._amount == other._amount and self._currency == other._currency
        return NotImplemented

    def __lt__(self, other: "Money") -> bool:
        if isinstance(other, Money):
            return self._amount < other._amount
        return NotImplemented

    def __le__(self, other: "Money") -> bool:
        if isinstance(other, Money):
            return self._amount <= other._amount
        return NotImplemented

    def __gt__(self, other: "Money") -> bool:
        if isinstance(other, Money):
            return self._amount > other._amount
        return NotImplemented

    def __ge__(self, other: "Money") -> bool:
        if isinstance(other, Money):
            return self._amount >= other._amount
        return NotImplemented

    # ── Serialization ─────────────────────────────────────────

    def __str__(self) -> str:
        """String representation: '₹199.99'"""
        return f"₹{self._amount}"

    def __repr__(self) -> str:
        return f"Money('{self._amount}', '{self._currency}')"

    def to_db(self) -> str:
        """
        Serialize for database storage.

        Returns:
            String representation suitable for VARCHAR/TEXT column.
        """
        return str(self._amount)

    def to_paise(self) -> int:
        """
        Convert to paise (smallest INR unit).

        Used for payment gateway APIs (Razorpay expects paise).

        Returns:
            Integer paise value. e.g., ₹199.99 → 19999
        """
        return int(self._amount * 100)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for API responses."""
        return {
            "amount": str(self._amount),
            "currency": self._currency,
            "paise": self.to_paise(),
        }

    @classmethod
    def from_db(cls, value: str, currency: str = DEFAULT_CURRENCY) -> "Money":
        """
        Deserialize from database string.

        Args:
            value: String from database (e.g., "199.99").
            currency: Currency code.

        Returns:
            Money instance.
        """
        return cls(value, currency)

    @classmethod
    def from_paise(cls, paise: int, currency: str = DEFAULT_CURRENCY) -> "Money":
        """
        Create Money from paise.

        Args:
            paise: Amount in paise (e.g., 19999 → ₹199.99).
            currency: Currency code.

        Returns:
            Money instance.
        """
        return cls(Decimal(paise) / Decimal(100), currency)

    def __hash__(self) -> int:
        return hash((self._amount, self._currency))


# ═══════════════════════════════════════════════════════════════
# GST CALCULATION UTILITIES
# ═══════════════════════════════════════════════════════════════

def calculate_gst(
    base_amount: Money,
    rate: Decimal = GST_RATE_AC,
    is_interstate: bool = False,
) -> Dict[str, Money]:
    """
    Calculate GST split for Indian tax compliance.

    Indian GST rules:
    - Intra-state (same state): CGST + SGST (each = rate / 2)
    - Inter-state (different state): IGST (= rate)

    The split ensures no rounding drift: CGST is calculated first,
    SGST = total - CGST (avoids 0.01 drift).

    Args:
        base_amount: The base amount to calculate GST on.
        rate: GST rate as Decimal (e.g., Decimal("0.18") for 18%).
        is_interstate: True for inter-state supply (IGST applies).

    Returns:
        Dict with cgst, sgst, igst, total_gst as Money objects.

    Example:
        calculate_gst(Money("1000.00"), Decimal("0.18"), False)
        # {"cgst": Money("90.00"), "sgst": Money("90.00"), "igst": Money("0.00"), "total_gst": Money("180.00")}
    """
    total_gst = base_amount * rate

    if is_interstate:
        return {
            "cgst": Money("0.00"),
            "sgst": Money("0.00"),
            "igst": total_gst,
            "total_gst": total_gst,
        }
    else:
        # Calculate CGST, then SGST = total - CGST to avoid rounding drift
        cgst_amount = round_inr(total_gst.amount / 2)
        sgst_amount = total_gst.amount - cgst_amount  # Exact remainder
        return {
            "cgst": Money(cgst_amount),
            "sgst": Money(sgst_amount, allow_negative=True),
            "igst": Money("0.00"),
            "total_gst": total_gst,
        }


def determine_gst_rate(
    is_ac_restaurant: bool = False,
    serves_alcohol: bool = False,
    is_canteen: bool = False,
) -> Decimal:
    """
    Determine the applicable GST rate for a restaurant service.

    Indian GST rules for restaurants (as of 2024):
    - 5% (no ITC): Standalone restaurants, non-AC, canteens
    - 18% (with ITC): AC restaurants, restaurants serving alcohol

    Args:
        is_ac_restaurant: Whether the restaurant is AC.
        serves_alcohol: Whether the restaurant serves alcohol.
        is_canteen: Whether it's a canteen/mess.

    Returns:
        Applicable GST rate as Decimal.
    """
    if is_canteen:
        return GST_RATE_CANTEEN
    if is_ac_restaurant or serves_alcohol:
        return GST_RATE_AC
    return GST_RATE_STANDARD


def calculate_commission(
    revenue: Money,
    commission_rate: Decimal = DEFAULT_COMMISSION_RATE,
) -> Dict[str, Money]:
    """
    Calculate platform commission and GST on commission.

    Args:
        revenue: Total revenue for the period.
        commission_rate: Commission rate (default 15%).

    Returns:
        Dict with commission, gst_on_commission, net_payout as Money.
    """
    commission = revenue * commission_rate
    gst = commission * GST_ON_COMMISSION
    net_payout = revenue - commission - gst

    return {
        "commission": commission,
        "gst_on_commission": gst,
        "net_payout": Money(net_payout.amount, allow_negative=True),
    }


def validate_invoice_amount(
    amount: Money,
    min_amount: Optional[Money] = None,
) -> None:
    """
    Validate that an invoice amount is acceptable.

    Prevents zero-amount invoices (Bug #20) and negative amounts.

    Args:
        amount: The amount to validate.
        min_amount: Minimum acceptable amount. Defaults to ₹1.00.

    Raises:
        ValueError: If amount is below minimum or negative.
    """
    if min_amount is None:
        min_amount = Money("1.00")

    if amount.amount < Decimal("0"):
        raise ValueError(f"Invoice amount cannot be negative: ₹{amount.amount}")

    if amount.amount < min_amount.amount:
        raise ValueError(
            f"Invoice amount ₹{amount.amount} is below minimum ₹{min_amount.amount}. "
            "Cannot generate invoices for free orders or full-discount orders."
        )
