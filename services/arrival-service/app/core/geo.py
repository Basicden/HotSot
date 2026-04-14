"""
HotSot Arrival Service — Geo / GPS Utilities
=============================================
Haversine distance calculation and GPS-based arrival validation.

The core rule: a user is considered *arrived* when their GPS position is
within `arrival_radius_m` metres of the kitchen / pickup location.
If the distance exceeds the threshold the arrival is recorded as a
*soft arrival* (logged but **no priority boost**).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.core.config import get_settings


# ── Data models ────────────────────────────────────────────────────────

class ArrivalType(str, Enum):
    """How the arrival was detected."""
    GPS = "gps"
    QR = "qr"


class ArrivalStrength(str, Enum):
    """Confidence level of the arrival detection."""
    HARD = "hard"   # Within radius — triggers priority boost
    SOFT = "soft"   # Outside radius — log only, no boost


@dataclass(frozen=True)
class GPSCoordinate:
    """A single GPS coordinate (WGS-84)."""
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"Latitude out of range: {self.latitude}")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"Longitude out of range: {self.longitude}")


@dataclass(frozen=True)
class ArrivalVerdict:
    """Result of arrival validation."""
    is_arrived: bool
    strength: ArrivalStrength
    distance_m: float
    arrival_type: ArrivalType
    threshold_m: float
    kitchen_coord: GPSCoordinate
    user_coord: Optional[GPSCoordinate]  # None for QR-based arrivals


# ── Haversine ──────────────────────────────────────────────────────────

_EARTH_RADIUS_M = 6_371_000.0  # Mean Earth radius in metres


def haversine(a: GPSCoordinate, b: GPSCoordinate) -> float:
    """
    Return the great-circle distance between two GPS coordinates in metres
    using the Haversine formula.

    Parameters
    ----------
    a, b : GPSCoordinate
        The two points to measure between.

    Returns
    -------
    float
        Distance in metres.
    """
    lat1, lon1 = math.radians(a.latitude), math.radians(a.longitude)
    lat2, lon2 = math.radians(b.latitude), math.radians(b.longitude)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    sin_dlat = math.sin(dlat / 2)
    sin_dlon = math.sin(dlon / 2)

    h = sin_dlat ** 2 + math.cos(lat1) * math.cos(lat2) * sin_dlon ** 2
    c = 2 * math.asin(math.sqrt(h))

    return _EARTH_RADIUS_M * c


# ── Arrival validation ─────────────────────────────────────────────────

def validate_gps_arrival(
    user_coord: GPSCoordinate,
    kitchen_coord: GPSCoordinate,
    arrival_radius_m: float | None = None,
) -> ArrivalVerdict:
    """
    Determine whether a user's GPS position qualifies as an arrival.

    * **HARD arrival** — distance ≤ threshold  →  priority boost eligible
    * **SOFT arrival** — distance > threshold   →  logged, no boost

    Parameters
    ----------
    user_coord : GPSCoordinate
        The user's current GPS position.
    kitchen_coord : GPSCoordinate
        The kitchen / pickup location.
    arrival_radius_m : float, optional
        Override the configured radius threshold.

    Returns
    -------
    ArrivalVerdict
    """
    settings = get_settings()
    threshold = arrival_radius_m if arrival_radius_m is not None else settings.arrival_radius_m

    distance = haversine(user_coord, kitchen_coord)
    is_hard = distance <= threshold

    return ArrivalVerdict(
        is_arrived=is_hard,
        strength=ArrivalStrength.HARD if is_hard else ArrivalStrength.SOFT,
        distance_m=round(distance, 2),
        arrival_type=ArrivalType.GPS,
        threshold_m=threshold,
        kitchen_coord=kitchen_coord,
        user_coord=user_coord,
    )


def validate_qr_arrival(
    kitchen_coord: GPSCoordinate,
) -> ArrivalVerdict:
    """
    A QR-scan arrival is always a HARD arrival — the user physically
    scanned a token displayed at the pickup counter.

    Parameters
    ----------
    kitchen_coord : GPSCoordinate
        The kitchen / pickup location.

    Returns
    -------
    ArrivalVerdict
    """
    return ArrivalVerdict(
        is_arrived=True,
        strength=ArrivalStrength.HARD,
        distance_m=0.0,
        arrival_type=ArrivalType.QR,
        threshold_m=0.0,
        kitchen_coord=kitchen_coord,
        user_coord=None,
    )
