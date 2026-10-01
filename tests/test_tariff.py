"""Rate D must reproduce the legacy backend's numbers exactly."""
from gate_cloud.tariff import RateD


def test_below_threshold_is_all_tier1():
    cost = RateD().cost(600, days=30)
    assert cost["tier1_kwh"] == 600 and cost["tier2_kwh"] == 0
    assert cost["energy_cost"] == round(600 * 0.0676, 4)
    assert cost["total"] == round(cost["energy_cost"] * 1.14975, 2)


def test_threshold_scales_with_the_period():
    one_day = RateD().cost(50, days=1, apply_taxes=False)
    assert (one_day["tier1_kwh"], one_day["tier2_kwh"]) == (40, 10)
    assert one_day["energy_cost"] == round(40 * 0.0676 + 10 * 0.1048, 4)


def test_fixed_charge_is_prorated():
    assert RateD().cost(0, days=15, apply_fixed_charge=True, apply_taxes=False)["fixed_cost"] == 6.27


def test_legacy_reference_value():
    # Output of calc_hydro_quebec_cost(1500, days=30), run from the legacy
    # backend source on 2026-10-01.
    assert RateD().cost(1500, days=30) == {
        "tier1_kwh": 1200.0, "tier2_kwh": 300.0, "energy_cost": 112.56, "fixed_cost": 0.0,
        "tax": 16.86, "total": 129.42, "effective_rate": 0.08628,
    }


def test_from_building_attributes_overrides_only_what_is_set():
    rate = RateD.from_attributes({"tariff_tier1_rate": "0.07", "monthly_budget": 150})
    assert rate.tier1_rate == 0.07
    assert rate.tier2_rate == RateD().tier2_rate
