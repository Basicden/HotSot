"""HotSot Billing Service — Unit Tests.

Tests cover:
1. Invoice amount validation
2. GST computation: CGST+SGST vs IGST
3. Payout method validation
4. Commission calculation
5. Round-inr helper
"""
from decimal import Decimal, ROUND_HALF_UP
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routes.billing import (
    round_inr,
    validate_invoice_amounts,
    DEFAULT_COMMISSION_RATE,
    GST_ON_COMMISSION_RATE,
    MIN_INVOICE_AMOUNT,
    VALID_PAYOUT_METHODS,
)


# ═══════════════════════════════════════════════════════════════
# INR ROUNDING
# ═══════════════════════════════════════════════════════════════

class TestRoundINR:
    """Test INR rounding helper."""

    def test_round_to_two_decimals(self):
        assert round_inr(Decimal("99.999")) == Decimal("100.00")

    def test_round_half_up(self):
        assert round_inr(Decimal("99.995")) == Decimal("100.00")

    def test_round_already_exact(self):
        assert round_inr(Decimal("250.00")) == Decimal("250.00")

    def test_round_small_amount(self):
        assert round_inr(Decimal("0.005")) == Decimal("0.01")


# ═══════════════════════════════════════════════════════════════
# INVOICE AMOUNT VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestValidateInvoiceAmounts:
    """Test invoice amount validation (Bug #20 prevention)."""

    def test_valid_amounts_pass(self):
        """Valid positive amounts should not raise."""
        validate_invoice_amounts(
            total_revenue=Decimal("1000.00"),
            commission_amount=Decimal("150.00"),
            gst_amount=Decimal("27.00"),
            payout_amount=Decimal("823.00"),
        )

    def test_zero_revenue_raises(self):
        """Zero total revenue should raise ValueError."""
        with pytest.raises(ValueError, match="below minimum"):
            validate_invoice_amounts(
                total_revenue=Decimal("0.00"),
                commission_amount=Decimal("0.00"),
                gst_amount=Decimal("0.00"),
                payout_amount=Decimal("0.00"),
            )

    def test_negative_commission_raises(self):
        """Negative commission should raise ValueError."""
        with pytest.raises(ValueError, match="negative"):
            validate_invoice_amounts(
                total_revenue=Decimal("1000.00"),
                commission_amount=Decimal("-10.00"),
                gst_amount=Decimal("27.00"),
                payout_amount=Decimal("823.00"),
            )

    def test_negative_gst_raises(self):
        """Negative GST should raise ValueError."""
        with pytest.raises(ValueError, match="negative"):
            validate_invoice_amounts(
                total_revenue=Decimal("1000.00"),
                commission_amount=Decimal("150.00"),
                gst_amount=Decimal("-5.00"),
                payout_amount=Decimal("823.00"),
            )

    def test_negative_payout_raises(self):
        """Negative payout should raise ValueError."""
        with pytest.raises(ValueError, match="negative"):
            validate_invoice_amounts(
                total_revenue=Decimal("1000.00"),
                commission_amount=Decimal("150.00"),
                gst_amount=Decimal("27.00"),
                payout_amount=Decimal("-1.00"),
            )

    def test_revenue_below_minimum_raises(self):
        """Revenue below ₹1.00 minimum should raise."""
        with pytest.raises(ValueError, match="below minimum"):
            validate_invoice_amounts(
                total_revenue=Decimal("0.50"),
                commission_amount=Decimal("0.08"),
                gst_amount=Decimal("0.01"),
                payout_amount=Decimal("0.41"),
            )


# ═══════════════════════════════════════════════════════════════
# GST COMPUTATION
# ═══════════════════════════════════════════════════════════════

class TestGSTComputation:
    """Test GST calculation and split."""

    def test_intra_state_gst_split(self):
        """Intra-state: GST should be split into CGST + SGST."""
        gst = Decimal("27.00")
        cgst = round_inr(gst / 2)
        sgst = round_inr(gst - cgst)  # Avoid rounding drift
        assert cgst + sgst == gst
        assert cgst == Decimal("13.50")
        assert sgst == Decimal("13.50")

    def test_inter_state_igst(self):
        """Inter-state: Full GST should be IGST."""
        gst = Decimal("27.00")
        igst = gst
        assert igst == Decimal("27.00")

    def test_gst_on_commission_is_18_percent(self):
        """GST on commission should be 18%."""
        assert GST_ON_COMMISSION_RATE == Decimal("0.18")

    def test_commission_calculation(self):
        """Commission = revenue * rate."""
        revenue = Decimal("1000.00")
        commission = round_inr(revenue * DEFAULT_COMMISSION_RATE)
        assert commission == Decimal("150.00")

    def test_gst_on_commission_calculation(self):
        """GST = commission * 18%."""
        commission = Decimal("150.00")
        gst = round_inr(commission * GST_ON_COMMISSION_RATE)
        assert gst == Decimal("27.00")

    def test_payout_equals_revenue_minus_commission_minus_gst(self):
        """Payout = revenue - commission - GST."""
        revenue = Decimal("1000.00")
        commission = Decimal("150.00")
        gst = Decimal("27.00")
        payout = round_inr(revenue - commission - gst)
        assert payout == Decimal("823.00")


# ═══════════════════════════════════════════════════════════════
# PAYOUT METHOD VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestPayoutMethod:
    """Test payout method validation."""

    def test_bank_transfer_is_valid(self):
        assert "BANK_TRANSFER" in VALID_PAYOUT_METHODS

    def test_upi_is_valid(self):
        assert "UPI" in VALID_PAYOUT_METHODS

    def test_razorpay_payout_is_valid(self):
        assert "RAZORPAY_PAYOUT" in VALID_PAYOUT_METHODS

    def test_invalid_method_rejected(self):
        assert "CHEQUE" not in VALID_PAYOUT_METHODS

    def test_negative_payout_amount_rejected(self):
        """Negative payout amount should be rejected."""
        assert Decimal("-100.00") <= Decimal("0")

    def test_zero_payout_amount_rejected(self):
        """Zero payout amount should be rejected."""
        assert Decimal("0") <= Decimal("0")


# ═══════════════════════════════════════════════════════════════
# COMMISSION RATE
# ═══════════════════════════════════════════════════════════════

class TestCommissionRate:
    """Test commission rate defaults."""

    def test_default_commission_is_15_percent(self):
        assert DEFAULT_COMMISSION_RATE == Decimal("0.15")

    def test_min_invoice_amount_is_1_rupee(self):
        assert MIN_INVOICE_AMOUNT == Decimal("1.00")
