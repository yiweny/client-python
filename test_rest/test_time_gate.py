import os
import unittest
from datetime import datetime, date

# We need to import before setting TIME_GATE to test the no-gate scenario
# Then test with gate set


class TimeGateParsingTest(unittest.TestCase):
    """Tests for parsing the TIME_GATE environment variable."""

    def setUp(self):
        # Clear TIME_GATE before each test
        if "TIME_GATE" in os.environ:
            del os.environ["TIME_GATE"]

    def tearDown(self):
        # Clean up after each test
        if "TIME_GATE" in os.environ:
            del os.environ["TIME_GATE"]

    def test_parse_time_gate_none(self):
        """Test that None returns None."""
        from polygon.rest.base import BaseClient

        result = BaseClient._parse_time_gate(None)
        self.assertIsNone(result)

    def test_parse_time_gate_empty_string(self):
        """Test that empty string returns None."""
        from polygon.rest.base import BaseClient

        result = BaseClient._parse_time_gate("")
        self.assertIsNone(result)

    def test_parse_time_gate_whitespace(self):
        """Test that whitespace-only string returns None."""
        from polygon.rest.base import BaseClient

        result = BaseClient._parse_time_gate("   ")
        self.assertIsNone(result)

    def test_parse_time_gate_date_string(self):
        """Test parsing ISO date string (YYYY-MM-DD)."""
        from polygon.rest.base import BaseClient

        result = BaseClient._parse_time_gate("2023-06-15")
        self.assertEqual(result, datetime(2023, 6, 15, 0, 0, 0))

    def test_parse_time_gate_datetime_string(self):
        """Test parsing ISO datetime string."""
        from polygon.rest.base import BaseClient

        result = BaseClient._parse_time_gate("2023-06-15T10:30:00")
        self.assertEqual(result, datetime(2023, 6, 15, 10, 30, 0))

    def test_parse_time_gate_datetime_with_microseconds(self):
        """Test parsing ISO datetime string with microseconds."""
        from polygon.rest.base import BaseClient

        result = BaseClient._parse_time_gate("2023-06-15T10:30:00.123456")
        self.assertEqual(result, datetime(2023, 6, 15, 10, 30, 0, 123456))

    def test_parse_time_gate_unix_seconds(self):
        """Test parsing Unix timestamp in seconds."""
        from polygon.rest.base import BaseClient

        # 2023-06-15 00:00:00 UTC
        result = BaseClient._parse_time_gate("1686787200")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2023)
        self.assertEqual(result.month, 6)

    def test_parse_time_gate_unix_milliseconds(self):
        """Test parsing Unix timestamp in milliseconds."""
        from polygon.rest.base import BaseClient

        # 2023-06-15 00:00:00 UTC in milliseconds
        result = BaseClient._parse_time_gate("1686787200000")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2023)
        self.assertEqual(result.month, 6)

    def test_parse_time_gate_with_whitespace(self):
        """Test that leading/trailing whitespace is stripped."""
        from polygon.rest.base import BaseClient

        result = BaseClient._parse_time_gate("  2023-06-15  ")
        self.assertEqual(result, datetime(2023, 6, 15, 0, 0, 0))


class TimeGateValueApplicationTest(unittest.TestCase):
    """Tests for applying time gate to individual values."""

    def setUp(self):
        # Set TIME_GATE for tests
        os.environ["TIME_GATE"] = "2023-06-15"
        # Import after setting env var
        from polygon.rest.base import BaseClient

        # Create a minimal mock client
        class MockClient(BaseClient):
            def __init__(self):
                self.time_gate = self._parse_time_gate(os.getenv("TIME_GATE"))

        self.client = MockClient()

    def tearDown(self):
        if "TIME_GATE" in os.environ:
            del os.environ["TIME_GATE"]

    def test_value_before_gate_unchanged(self):
        """Test that values before the time gate are unchanged."""
        result = self.client._apply_time_gate_to_value("2023-06-10")
        self.assertEqual(result, "2023-06-10")

    def test_value_after_gate_capped(self):
        """Test that values after the time gate are capped."""
        result = self.client._apply_time_gate_to_value("2023-06-20")
        self.assertEqual(result, "2023-06-15")

    def test_value_on_gate_unchanged(self):
        """Test that values exactly on the time gate are unchanged."""
        result = self.client._apply_time_gate_to_value("2023-06-15")
        self.assertEqual(result, "2023-06-15")

    def test_datetime_before_gate_unchanged(self):
        """Test that datetime before gate is unchanged."""
        dt = datetime(2023, 6, 10)
        result = self.client._apply_time_gate_to_value(dt)
        self.assertEqual(result, dt)

    def test_datetime_after_gate_capped(self):
        """Test that datetime after gate is capped."""
        dt = datetime(2023, 6, 20)
        result = self.client._apply_time_gate_to_value(dt)
        self.assertEqual(result, datetime(2023, 6, 15))

    def test_date_before_gate_unchanged(self):
        """Test that date before gate is unchanged."""
        d = date(2023, 6, 10)
        result = self.client._apply_time_gate_to_value(d)
        self.assertEqual(result, d)

    def test_date_after_gate_capped(self):
        """Test that date after gate is capped."""
        d = date(2023, 6, 20)
        result = self.client._apply_time_gate_to_value(d)
        self.assertEqual(result, date(2023, 6, 15))

    def test_timestamp_millis_before_gate_unchanged(self):
        """Test that millisecond timestamp before gate is unchanged."""
        ts = int(datetime(2023, 6, 10).timestamp() * 1000)
        result = self.client._apply_time_gate_to_value(ts, "millis")
        self.assertEqual(result, ts)

    def test_timestamp_millis_after_gate_capped(self):
        """Test that millisecond timestamp after gate is capped."""
        ts_before = int(datetime(2023, 6, 20).timestamp() * 1000)
        result = self.client._apply_time_gate_to_value(ts_before, "millis")
        expected = int(datetime(2023, 6, 15).timestamp() * 1000)
        self.assertEqual(result, expected)

    def test_none_value_unchanged(self):
        """Test that None value is unchanged."""
        result = self.client._apply_time_gate_to_value(None)
        self.assertIsNone(result)


class TimeGateNoGateTest(unittest.TestCase):
    """Tests for behavior when TIME_GATE is not set."""

    def setUp(self):
        # Ensure TIME_GATE is not set
        if "TIME_GATE" in os.environ:
            del os.environ["TIME_GATE"]
        from polygon.rest.base import BaseClient

        class MockClient(BaseClient):
            def __init__(self):
                self.time_gate = self._parse_time_gate(os.getenv("TIME_GATE"))

        self.client = MockClient()

    def test_no_gate_value_unchanged(self):
        """Test that values pass through unchanged when no gate is set."""
        result = self.client._apply_time_gate_to_value("2030-12-31")
        self.assertEqual(result, "2030-12-31")

    def test_no_gate_params_unchanged(self):
        """Test that params pass through unchanged when no gate is set."""
        params = {"timestamp.gte": "2023-01-01", "timestamp.lte": "2030-12-31"}
        result = self.client._apply_time_gate_to_params(params.copy())
        self.assertEqual(result, params)


class TimeGateParamsApplicationTest(unittest.TestCase):
    """Tests for applying time gate to query parameters."""

    def setUp(self):
        os.environ["TIME_GATE"] = "2023-06-15"
        from polygon.rest.base import BaseClient

        class MockClient(BaseClient):
            def __init__(self):
                self.time_gate = self._parse_time_gate(os.getenv("TIME_GATE"))

        self.client = MockClient()

    def tearDown(self):
        if "TIME_GATE" in os.environ:
            del os.environ["TIME_GATE"]

    def test_caps_timestamp_lte_after_gate(self):
        """Test that timestamp.lte after gate is capped."""
        params = {"timestamp.gte": "2023-01-01", "timestamp.lte": "2023-12-31"}
        result = self.client._apply_time_gate_to_params(params)
        self.assertEqual(result["timestamp.lte"], "2023-06-15")

    def test_timestamp_lte_before_gate_unchanged(self):
        """Test that timestamp.lte before gate is unchanged."""
        params = {"timestamp.gte": "2023-01-01", "timestamp.lte": "2023-05-01"}
        result = self.client._apply_time_gate_to_params(params)
        self.assertEqual(result["timestamp.lte"], "2023-05-01")

    def test_adds_timestamp_lte_when_missing(self):
        """Test that timestamp.lte is added when timestamp params exist without upper bound."""
        params = {"timestamp.gte": "2023-01-01"}
        result = self.client._apply_time_gate_to_params(params)
        self.assertIn("timestamp.lte", result)

    def test_does_not_add_lte_when_lt_exists(self):
        """Test that timestamp.lte is not added when timestamp.lt already exists."""
        params = {"timestamp.gte": "2023-01-01", "timestamp.lt": "2023-05-01"}
        result = self.client._apply_time_gate_to_params(params)
        self.assertNotIn("timestamp.lte", result)
        self.assertIn("timestamp.lt", result)

    def test_caps_published_utc_lte(self):
        """Test that published_utc.lte is capped."""
        params = {"published_utc.gte": "2023-01-01", "published_utc.lte": "2023-12-31"}
        result = self.client._apply_time_gate_to_params(params)
        self.assertEqual(result["published_utc.lte"], "2023-06-15")

    def test_caps_filing_date_lte(self):
        """Test that filing_date.lte is capped."""
        params = {"filing_date.gte": "2023-01-01", "filing_date.lte": "2023-12-31"}
        result = self.client._apply_time_gate_to_params(params)
        self.assertEqual(result["filing_date.lte"], "2023-06-15")

    def test_caps_execution_date_lte(self):
        """Test that execution_date.lte is capped."""
        params = {
            "execution_date.gte": "2023-01-01",
            "execution_date.lte": "2023-12-31",
        }
        result = self.client._apply_time_gate_to_params(params)
        self.assertEqual(result["execution_date.lte"], "2023-06-15")

    def test_unrelated_params_unchanged(self):
        """Test that unrelated params are unchanged."""
        params = {"ticker": "AAPL", "limit": 100}
        result = self.client._apply_time_gate_to_params(params)
        self.assertEqual(result["ticker"], "AAPL")
        self.assertEqual(result["limit"], 100)

    def test_adds_timestamp_lte_even_without_any_timestamp_params(self):
        """Test that timestamp.lte is always added when time gate is set, even with no timestamp params."""
        params = {"ticker": "AAPL", "limit": 100}
        result = self.client._apply_time_gate_to_params(params)
        self.assertIn("timestamp.lte", result)

    def test_adds_timestamp_lte_to_empty_params(self):
        """Test that timestamp.lte is added even to empty params."""
        params = {}
        result = self.client._apply_time_gate_to_params(params)
        self.assertIn("timestamp.lte", result)


class TimeGateAggsTest(unittest.TestCase):
    """Tests for time gate application in aggs endpoints."""

    def setUp(self):
        os.environ["TIME_GATE"] = "2023-06-15"
        from polygon.rest.aggs import AggsClient

        class MockAggsClient(AggsClient):
            def __init__(self):
                self.time_gate = self._parse_time_gate(os.getenv("TIME_GATE"))

        self.client = MockAggsClient()

    def tearDown(self):
        if "TIME_GATE" in os.environ:
            del os.environ["TIME_GATE"]

    def test_agg_date_string_before_gate_unchanged(self):
        """Test that date string before gate is unchanged."""
        result = self.client._apply_time_gate_to_agg_date("2023-06-10")
        self.assertEqual(result, "2023-06-10")

    def test_agg_date_string_after_gate_capped(self):
        """Test that date string after gate is capped."""
        result = self.client._apply_time_gate_to_agg_date("2023-06-20")
        self.assertEqual(result, "2023-06-15")

    def test_agg_date_object_before_gate_unchanged(self):
        """Test that date object before gate is unchanged."""
        d = date(2023, 6, 10)
        result = self.client._apply_time_gate_to_agg_date(d)
        self.assertEqual(result, d)

    def test_agg_date_object_after_gate_capped(self):
        """Test that date object after gate is capped."""
        d = date(2023, 6, 20)
        result = self.client._apply_time_gate_to_agg_date(d)
        self.assertEqual(result, date(2023, 6, 15))

    def test_agg_datetime_before_gate_unchanged(self):
        """Test that datetime before gate is unchanged."""
        dt = datetime(2023, 6, 10)
        result = self.client._apply_time_gate_to_agg_date(dt)
        self.assertEqual(result, dt)

    def test_agg_datetime_after_gate_capped(self):
        """Test that datetime after gate is capped."""
        dt = datetime(2023, 6, 20)
        result = self.client._apply_time_gate_to_agg_date(dt)
        self.assertEqual(result, datetime(2023, 6, 15))

    def test_agg_timestamp_millis_before_gate_unchanged(self):
        """Test that millisecond timestamp before gate is unchanged."""
        ts = int(datetime(2023, 6, 10).timestamp() * 1000)
        result = self.client._apply_time_gate_to_agg_date(ts)
        self.assertEqual(result, ts)

    def test_agg_timestamp_millis_after_gate_capped(self):
        """Test that millisecond timestamp after gate is capped."""
        ts = int(datetime(2023, 6, 20).timestamp() * 1000)
        result = self.client._apply_time_gate_to_agg_date(ts)
        expected = int(datetime(2023, 6, 15).timestamp() * 1000)
        self.assertEqual(result, expected)


class TimeGateAggsNoGateTest(unittest.TestCase):
    """Tests for aggs behavior when TIME_GATE is not set."""

    def setUp(self):
        if "TIME_GATE" in os.environ:
            del os.environ["TIME_GATE"]
        from polygon.rest.aggs import AggsClient

        class MockAggsClient(AggsClient):
            def __init__(self):
                self.time_gate = self._parse_time_gate(os.getenv("TIME_GATE"))

        self.client = MockAggsClient()

    def test_no_gate_date_unchanged(self):
        """Test that dates pass through unchanged when no gate is set."""
        result = self.client._apply_time_gate_to_agg_date("2030-12-31")
        self.assertEqual(result, "2030-12-31")

    def test_no_gate_datetime_unchanged(self):
        """Test that datetimes pass through unchanged when no gate is set."""
        dt = datetime(2030, 12, 31)
        result = self.client._apply_time_gate_to_agg_date(dt)
        self.assertEqual(result, dt)


if __name__ == "__main__":
    unittest.main()
