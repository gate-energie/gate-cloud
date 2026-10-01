"""Hydro-Quebec Rate D, carried over from the legacy GATE backend.

Same arithmetic as gate/backend/app/services/settings_service.py
(calc_hydro_quebec_cost), so costs computed here match the ones the old
dashboard showed. The parameters now come from the Building asset's
SERVER_SCOPE attributes instead of Firestore; `from_attributes` reads the keys
the asset model writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RateD:
    tier1_rate: float = 0.0676  # $/kWh up to the daily threshold
    tier2_rate: float = 0.1048  # $/kWh above it
    tier1_limit_kwh_day: float = 40.0
    fixed_charge_month: float = 12.54
    tax_rate: float = 0.14975  # QC TPS + TVQ combined

    @classmethod
    def from_attributes(cls, attributes: Mapping[str, Any]) -> "RateD":
        """Build from Building attributes; a missing key keeps the default."""
        fields = {
            "tier1_rate": "tariff_tier1_rate",
            "tier2_rate": "tariff_tier2_rate",
            "tier1_limit_kwh_day": "tariff_tier1_limit_kwh_day",
            "fixed_charge_month": "tariff_fixed_charge_month",
            "tax_rate": "tariff_tax_rate",
        }
        return cls(**{field: float(attributes[key]) for field, key in fields.items() if key in attributes})

    def cost(self, kwh: float, days: int = 30, apply_fixed_charge: bool = False, apply_taxes: bool = True) -> dict[str, float]:
        """Cost of `kwh` consumed over a billing period of `days`.

        The tier-1 threshold scales with the period: 40 kWh/day is 1 200 kWh
        over 30 days, 40 kWh over one.
        """
        tier1_limit = self.tier1_limit_kwh_day * days
        tier1_kwh = min(kwh, tier1_limit)
        tier2_kwh = max(0.0, kwh - tier1_limit)

        energy_cost = round(tier1_kwh * self.tier1_rate + tier2_kwh * self.tier2_rate, 4)
        fixed_cost = round(self.fixed_charge_month * days / 30, 2) if apply_fixed_charge else 0.0
        subtotal = energy_cost + fixed_cost
        tax = round(subtotal * self.tax_rate, 2) if apply_taxes else 0.0
        total = round(subtotal + tax, 2)
        return {
            "tier1_kwh": round(tier1_kwh, 2),
            "tier2_kwh": round(tier2_kwh, 2),
            "energy_cost": energy_cost,
            "fixed_cost": fixed_cost,
            "tax": tax,
            "total": total,
            "effective_rate": round(total / kwh, 5) if kwh > 0 else 0.0,
        }
