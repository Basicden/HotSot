"""HotSot Arrival Service — Geofence & Distance Calculations."""

import math
from typing import Optional


def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate distance between two GPS coordinates using Haversine formula.

    Returns distance in meters.
    """
    R = 6371000  # Earth radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)

    a = math.sin(delta_phi / 2) ** 2 +         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def is_within_geofence(user_lat: float, user_lng: float,
                        kitchen_lat: float, kitchen_lng: float,
                        radius_m: float = 150.0) -> bool:
    """Check if user is within geofence radius of kitchen.

    Default radius: 150 meters (walkable distance in Indian commercial areas).
    """
    distance = haversine_distance(user_lat, user_lng, kitchen_lat, kitchen_lng)
    return distance <= radius_m


def classify_proximity(distance_meters: float) -> str:
    """Classify user proximity level.

    Returns:
        IMMEDIATE: <50m (at the counter)
        NEARBY: <150m (approaching)
        APPROACHING: <500m (on the way)
        FAR: >500m (not yet close)
    """
    if distance_meters < 50:
        return "IMMEDIATE"
    elif distance_meters < 150:
        return "NEARBY"
    elif distance_meters < 500:
        return "APPROACHING"
    return "FAR"
