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
    cats = factors.get("material_categories", {})
    c = cats.get(material) or cats.get("_default", {})
    return float(c.get("density_t_per_m3", 1.6))


def _payload(factors, vehicle):
    vs = factors.get("vehicles", {})
    v = vs.get(vehicle) or vs.get("_default", {})
    return float(v.get("payload_t", 20))


def _emissions(factors, vehicle):
    vs = factors.get("vehicles", {})
    v = vs.get(vehicle) or vs.get("_default", {})
    return float(v.get("emissions_kg_co2e_per_km", 0.90))


def material_names(factors):
    return [k for k in factors.get("material_categories", {}) if not k.startswith("_")]


def vehicle_names(factors):
    return [k for k in factors.get("vehicles", {}) if not k.startswith("_")]


def flat_factors(factors):
    """
    Backward-compatible flat maps for clients that read the old shape
    (the map and the dashboard read factors.vehicle_payload_t etc.).
    Derived from the rich taxonomy so there is still one source of truth.
    """
    cats = factors.get("material_categories", {})
    vs = factors.get("vehicles", {})
    density = {k: v.get("density_t_per_m3", 1.6) for k, v in cats.items()}
    density.setdefault("_default", 1.6)
    payload = {k: v.get("payload_t", 20) for k, v in vs.items()}
    payload.setdefault("_default", 20)
    emis = {k: v.get("emissions_kg_co2e_per_km", 0.90) for k, v in vs.items()}
    emis.setdefault("_default", 0.90)
    return {
        "material_density_t_per_m3": density,
        "vehicle_payload_t": payload,
        "vehicle_emissions_kg_co2e_per_km": emis,
        "planning": factors.get("planning", {}),
    }


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


def effective_payload(factors, v1, v2=None, split_pct=100):
    """
    Blended payload when a load is split across two vehicle types.
    split_pct is the % of tonnage carried by v1; v2 carries the rest.
    trips-per-tonne = s/p1 + (1-s)/p2  ->  effective payload = 1 / that.
    """
    p1 = _payload(factors, v1)
    if not v2 or split_pct is None or split_pct >= 100:
        return p1
    p2 = _payload(factors, v2)
    s = max(0, min(100, int(split_pct))) / 100.0
    inv = s / p1 + (1 - s) / p2
    return (1.0 / inv) if inv > 0 else p1


def convert_row(row, to_unit, factors=None):
    """
    Convert one stored forecast row to the requested unit, honouring a second
    vehicle + split when present. Rows submitted with two vehicles are always
    stored in m3 or t (never 'vehicles'), so the input side needs no split.
    """
    factors = factors or load_factors()
    material = row.get("material_type")
    tonnes = to_tonnes(row["quantity"], row["unit"], material, row.get("vehicle_type"), factors)
    if to_unit == "t":
        return tonnes
    if to_unit == "m3":
        return tonnes / _density(factors, material)
    if to_unit == "vehicles":
        ep = effective_payload(factors, row.get("vehicle_type"),
                               row.get("vehicle_type_2"), row.get("split_pct", 100))
        return tonnes / ep
    raise ValueError(f"Unknown unit: {to_unit}")


def round_for_unit(value, unit):
    """Vehicles are whole; volumes/weights get one decimal."""
    if unit == "vehicles":
        return int(round(value))
    return round(float(value), 1)
