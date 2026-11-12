"""A monthly movement that names a balance ("Churned ARR", "Headcount Cost") adds up
over a quarter; a true balance takes its last month."""

import pytest

from app.compute.aggregate import infer_aggregation_type


@pytest.mark.parametrize("name", ["Churned ARR", "Headcount Cost", "New Customers", "Payroll Expense",
                                  "Net New ARR", "Customers Lost", "Revenue"])
def test_movements_are_flows(name):
    assert infer_aggregation_type(name, "USD") == "flow"


@pytest.mark.parametrize("name", ["ARR", "Cash Balance", "Headcount", "Active Customers", "Runway"])
def test_balances_stay_balances(name):
    assert infer_aggregation_type(name, "USD") == "balance"
