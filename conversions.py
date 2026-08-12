"""
Unit conversion engine for logistics forecasting.

Everything pivots through TONNES:
    m3       -> tonnes  via material density
    tonnes   -> tonnes  (identity)
    vehicles -> tonnes  via vehicle payload

...and back out to whichever unit the caller wants. All the tunable numbers
live in factors.json so an engineer can adjust them without touching code.
"""
import json
import os

UNITS = ["m3", "t", "vehicles"]
_FACTORS_PATH = os.path.join(os.path.dirname(__file__), "factors.json")


def load_factors():
    """Read factors.json fresh each call so edits take effect on redeploy."""
    with open(_FACTORS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _density(factors, material):
    d = factors["material_density_t_per_m3"]
    return float(d.get(material, d["_default"]))


def _payload(factors, vehicle):
    p = factors["vehicle_payload_t"]
    return float(p.get(vehicle, p["_default"]))


def to_tonnes(quantity, unit, material, vehicle, factors=None):
    factors = factors or load_factors()
    q = float(quantity)
    if unit == "t":
        return q
    if unit == "m3":
        return q * _density(factors, material)
    if unit == "vehicles":
        return q * _payload(factors, vehicle)
    raise ValueError(f"Unknown unit: {unit}")


def from_tonnes(tonnes, unit, material, vehicle, factors=None):
    factors = factors or load_factors()
    t = float(tonnes)
    if unit == "t":
        return t
    if unit == "m3":
        return t / _density(factors, material)
    if unit == "vehicles":
        return t / _payload(factors, vehicle)
    raise ValueError(f"Unknown unit: {unit}")


def convert(quantity, from_unit, to_unit, material, vehicle, factors=None):
    """Convert a quantity from one unit to another for a given material/vehicle."""
    factors = factors or load_factors()
    tonnes = to_tonnes(quantity, from_unit, material, vehicle, factors)
    return from_tonnes(tonnes, to_unit, material, vehicle, factors)


def round_for_unit(value, unit):
    """Vehicles are whole; volumes/weights get one decimal."""
    if unit == "vehicles":
        return int(round(value))
    return round(float(value), 1)
