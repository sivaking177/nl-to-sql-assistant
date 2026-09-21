"""Explainer tests: the breakdown must name the real tables, filters,
aggregations, grouping, and limit of the queries this system produces."""

from nlsql.core.explain import explain_sql


def test_simple_aggregate_with_filter():
    explanation = explain_sql(
        "SELECT COUNT(*) AS n FROM trips WHERE payment_type = 1 LIMIT 1000"
    )
    assert explanation.tables == ["trips"]
    assert explanation.joins == 0
    assert explanation.aggregations == ["COUNT"]
    assert explanation.filters == ["payment_type = 1"]
    assert explanation.limit == 1000
    assert "Reads from trips" in explanation.summary()


def test_join_group_and_order():
    explanation = explain_sql(
        "SELECT z.Zone AS zone, COUNT(*) AS trips FROM trips t "
        "JOIN taxi_zones z ON z.LocationID = t.DOLocationID "
        "GROUP BY z.Zone ORDER BY trips DESC LIMIT 5"
    )
    assert explanation.tables == ["trips", "taxi_zones"]
    assert explanation.joins == 1
    assert explanation.group_by == ["z.Zone"]
    assert explanation.order_by == "trips DESC"
    assert explanation.limit == 5


def test_group_by_function_call_is_not_split_on_its_comma():
    explanation = explain_sql(
        "SELECT date_trunc('day', tpep_pickup_datetime) AS d, COUNT(*) AS n "
        "FROM trips GROUP BY d ORDER BY n DESC LIMIT 1"
    )
    assert explanation.group_by == ["d"]
    assert explanation.aggregations == ["COUNT"]


def test_multiple_filters_split_on_top_level_and():
    explanation = explain_sql(
        "SELECT AVG(fare_amount) FROM trips "
        "WHERE passenger_count > 2 AND fare_amount < 100"
    )
    assert len(explanation.filters) == 2
    assert explanation.aggregations == ["AVG"]


def test_to_dict_shape():
    payload = explain_sql("SELECT SUM(total_amount) FROM trips").to_dict()
    assert payload["tables"] == ["trips"]
    assert payload["aggregations"] == ["SUM"]
    assert payload["limit"] is None
    assert payload["summary"].endswith(".")
