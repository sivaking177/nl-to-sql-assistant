"""Schema-introspection and prompt tests: the prompt is the product, so it
gets asserted on like code."""

from nlsql.core.schema import build_system_prompt, introspect_schema


def test_introspection_finds_tables_and_columns(db):
    schema = introspect_schema(db.connection)
    trips = schema.table("trips")
    assert trips.row_count == db.row_count("trips")
    col_names = [c.name for c in trips.columns]
    assert "fare_amount" in col_names
    assert "tpep_pickup_datetime" in col_names
    zones = schema.table("taxi_zones")
    assert "Zone" in [c.name for c in zones.columns]


def test_low_cardinality_values_are_enumerated(db):
    schema = introspect_schema(db.connection)
    payment = next(c for c in schema.table("trips").columns if c.name == "payment_type")
    # payment_type has a handful of codes; they should be enumerated.
    assert "1" in payment.sample_values
    assert len(payment.sample_values) <= 25


def test_high_cardinality_values_are_not_enumerated(db):
    schema = introspect_schema(db.connection)
    fare = next(c for c in schema.table("trips").columns if c.name == "fare_amount")
    assert fare.sample_values == []


def test_prompt_contains_schema_and_rules(db):
    prompt = build_system_prompt(introspect_schema(db.connection))
    assert "TABLE trips" in prompt
    assert "TABLE taxi_zones" in prompt
    assert "fare_amount" in prompt
    assert "PULocationID" in prompt
    assert "single read-only SELECT" in prompt
    # Domain notes the model needs for the payment codes.
    assert "1 = credit card" in prompt
