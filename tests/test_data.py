"""Data-layer tests: the loader must produce the real TLC schema and the
row count the sample was built with (48,601 rows, January 2024)."""

EXPECTED_TRIP_COLUMNS = {
    "VendorID",
    "tpep_pickup_datetime",
    "tpep_dropoff_datetime",
    "passenger_count",
    "trip_distance",
    "RatecodeID",
    "store_and_fwd_flag",
    "PULocationID",
    "DOLocationID",
    "payment_type",
    "fare_amount",
    "extra",
    "mta_tax",
    "tip_amount",
    "tolls_amount",
    "improvement_surcharge",
    "total_amount",
    "congestion_surcharge",
    "Airport_fee",
}

EXPECTED_SAMPLE_ROWS = 48_601


def test_tables_loaded(db):
    assert db.table_names() == ["taxi_zones", "trips"]


def test_trips_row_count(db):
    assert db.row_count("trips") == EXPECTED_SAMPLE_ROWS


def test_zones_row_count(db):
    # The TLC lookup has 265 zones.
    assert db.row_count("taxi_zones") == 265


def test_trips_schema_matches_tlc(db):
    _, rows = db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'trips'"
    )
    assert {r[0] for r in rows} == EXPECTED_TRIP_COLUMNS


def test_pickups_are_all_january_2024(db):
    _, rows = db.execute(
        "SELECT min(tpep_pickup_datetime), max(tpep_pickup_datetime) FROM trips"
    )
    earliest, latest = rows[0]
    assert str(earliest).startswith("2024-01-01")
    assert str(latest).startswith("2024-01-31")


def test_jfk_is_zone_132(db):
    _, rows = db.execute("SELECT Zone FROM taxi_zones WHERE LocationID = 132")
    assert rows[0][0] == "JFK Airport"
