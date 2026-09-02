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


def planning_vehicle_names(factors):
    """
    The four EU-named planning vehicles, in the order factors.json lists them.

    Two sources on purpose, and they have to agree: the top-level `planning_vehicles`
    array carries the ORDER, and `planning_vehicle: true` on the entry itself is the
    FLAG. A name in the array that is not a real vehicle key is DROPPED rather than
    returned — it would otherwise reach a picker as an option that silently resolves to
    _default (20 t, 0.90 kg/km), which is the failure factors_diagnostics() exists to
    catch. An entry that is flagged but missing from the array is appended, so flagging
    a fifth vehicle is one edit and forgetting the array is visible, not silent.
    """
    vs = factors.get("vehicles", {}) or {}
    known = [k for k in vs if not k.startswith("_")]
    out = [n for n in (factors.get("planning_vehicles") or []) if n in known]
    for k in known:
        if k not in out and (vs.get(k) or {}).get("planning_vehicle"):
            out.append(k)
    return out


VEHICLE_LANGS = ("en", "eu", "ee")


def vehicle_labels(factors):
    """
    {vehicle_key: {"en": ..., "eu": ..., "ee": ...}} with every slot filled.

    A missing label falls back to the KEY — never to another language and never to an
    invented trade name (the 2026-09-02 feedback is explicit: fall back to the EU string
    rather than invent). The key is the canonical id a forecast line stores; a label only
    changes what a picker shows. `fallbacks` says which slots were filled that way, so a
    UI can mark them rather than pass a fallback off as a translation.
    """
    out, fallbacks = {}, {}
    for k, v in (factors.get("vehicles", {}) or {}).items():
        if k.startswith("_"):
            continue
        lab = (v or {}).get("labels") or {}
        out[k] = {}
        for lang in VEHICLE_LANGS:
            val = (lab.get(lang) or "").strip() if isinstance(lab.get(lang), str) else ""
            if val:
                out[k][lang] = val
            else:
                out[k][lang] = k
                fallbacks.setdefault(k, []).append(lang)
    return {"labels": out, "fallbacks": fallbacks, "langs": list(VEHICLE_LANGS)}


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
    DEPRECATED in Phase 2 — kept only so an older caller does not crash.

    A load split across two vehicle types is now TWO forecast lines, one vehicle each,
    which the widened forecasts key makes storable. Blending two payloads into one number
    also blended their haul cycles, and those are not close: an Artic Flatbed turns round
    in 45 minutes against an Artic Tipper's 24. Two lines keep each cycle honest.

    With v2 omitted this is just v1's payload, which is all any current caller passes.
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
    Convert one stored forecast row to the requested unit.

    One vehicle per row since Phase 2 — a split is two rows, so there is no blend here
    any more. Falls back through effective_payload() only if a legacy row still carries
    vehicle_type_2, which the rebuilt table cannot produce.
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
