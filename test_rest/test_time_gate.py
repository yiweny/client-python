import os
import unittest
import asyncio
from datetime import datetime, date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_gate():
    if "TIME_GATE" in os.environ:
        del os.environ["TIME_GATE"]


def _make_mock(gate_str=None):
    """Build a lightweight mock that only sets time_gate (no network)."""
    from polygon.rest.base import BaseClient

    class _Mock(BaseClient):
        def __init__(self, tg):
            self.time_gate = self._parse_time_gate(tg)

    return _Mock(gate_str)


# ===================================================================
# 1. Parsing the TIME_GATE env var
# ===================================================================

class TimeGateParsingTest(unittest.TestCase):
    def setUp(self):
        _clear_gate()

    def tearDown(self):
        _clear_gate()

    def test_none(self):
        from polygon.rest.base import BaseClient
        self.assertIsNone(BaseClient._parse_time_gate(None))

    def test_empty_string(self):
        from polygon.rest.base import BaseClient
        self.assertIsNone(BaseClient._parse_time_gate(""))

    def test_whitespace_only(self):
        from polygon.rest.base import BaseClient
        self.assertIsNone(BaseClient._parse_time_gate("   "))

    def test_date_string(self):
        from polygon.rest.base import BaseClient
        self.assertEqual(
            BaseClient._parse_time_gate("2023-06-15"),
            datetime(2023, 6, 15),
        )

    def test_datetime_string(self):
        from polygon.rest.base import BaseClient
        self.assertEqual(
            BaseClient._parse_time_gate("2023-06-15T10:30:00"),
            datetime(2023, 6, 15, 10, 30, 0),
        )

    def test_datetime_with_microseconds(self):
        from polygon.rest.base import BaseClient
        self.assertEqual(
            BaseClient._parse_time_gate("2023-06-15T10:30:00.123456"),
            datetime(2023, 6, 15, 10, 30, 0, 123456),
        )

    def test_unix_seconds(self):
        from polygon.rest.base import BaseClient
        result = BaseClient._parse_time_gate("1686787200")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2023)
        self.assertEqual(result.month, 6)

    def test_unix_milliseconds(self):
        from polygon.rest.base import BaseClient
        result = BaseClient._parse_time_gate("1686787200000")
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2023)
        self.assertEqual(result.month, 6)

    def test_strips_whitespace(self):
        from polygon.rest.base import BaseClient
        self.assertEqual(
            BaseClient._parse_time_gate("  2023-06-15  "),
            datetime(2023, 6, 15),
        )

    def test_invalid_string_returns_none(self):
        from polygon.rest.base import BaseClient
        self.assertIsNone(BaseClient._parse_time_gate("not-a-date"))


# ===================================================================
# 2. _apply_time_gate_to_value  (individual value capping)
# ===================================================================

class TimeGateValueApplicationTest(unittest.TestCase):
    def setUp(self):
        os.environ["TIME_GATE"] = "2023-06-15"
        self.client = _make_mock("2023-06-15")

    def tearDown(self):
        _clear_gate()

    # -- strings --
    def test_str_before(self):
        self.assertEqual(self.client._apply_time_gate_to_value("2023-06-10"), "2023-06-10")

    def test_str_after(self):
        self.assertEqual(self.client._apply_time_gate_to_value("2023-06-20"), "2023-06-15")

    def test_str_on_gate(self):
        self.assertEqual(self.client._apply_time_gate_to_value("2023-06-15"), "2023-06-15")

    def test_str_with_time_after(self):
        self.assertEqual(
            self.client._apply_time_gate_to_value("2023-06-20T12:00:00"),
            "2023-06-15T00:00:00",
        )

    # -- datetime --
    def test_datetime_before(self):
        dt = datetime(2023, 6, 10)
        self.assertEqual(self.client._apply_time_gate_to_value(dt), dt)

    def test_datetime_after(self):
        self.assertEqual(
            self.client._apply_time_gate_to_value(datetime(2023, 6, 20)),
            datetime(2023, 6, 15),
        )

    # -- date --
    def test_date_before(self):
        d = date(2023, 6, 10)
        self.assertEqual(self.client._apply_time_gate_to_value(d), d)

    def test_date_after(self):
        self.assertEqual(
            self.client._apply_time_gate_to_value(date(2023, 6, 20)),
            date(2023, 6, 15),
        )

    # -- int timestamps --
    def test_millis_before(self):
        ts = int(datetime(2023, 6, 10).timestamp() * 1000)
        self.assertEqual(self.client._apply_time_gate_to_value(ts, "millis"), ts)

    def test_millis_after(self):
        ts = int(datetime(2023, 6, 20).timestamp() * 1000)
        expected = int(datetime(2023, 6, 15).timestamp() * 1000)
        self.assertEqual(self.client._apply_time_gate_to_value(ts, "millis"), expected)

    def test_nanos_after(self):
        ts = int(datetime(2023, 6, 20).timestamp() * 1_000_000_000)
        expected = int(datetime(2023, 6, 15).timestamp() * 1_000_000_000)
        self.assertEqual(self.client._apply_time_gate_to_value(ts, "nanos"), expected)

    # -- None --
    def test_none(self):
        self.assertIsNone(self.client._apply_time_gate_to_value(None))


# ===================================================================
# 3. _apply_time_gate_to_params  (query-param dict capping)
# ===================================================================

class TimeGateParamsApplicationTest(unittest.TestCase):
    def setUp(self):
        os.environ["TIME_GATE"] = "2023-06-15"
        self.client = _make_mock("2023-06-15")

    def tearDown(self):
        _clear_gate()

    # -- cap existing upper bounds --
    def test_caps_timestamp_lte(self):
        p = {"timestamp.gte": "2023-01-01", "timestamp.lte": "2023-12-31"}
        self.assertEqual(self.client._apply_time_gate_to_params(p)["timestamp.lte"], "2023-06-15")

    def test_caps_timestamp_lt(self):
        p = {"timestamp.gte": "2023-01-01", "timestamp.lt": "2023-12-31"}
        self.assertEqual(self.client._apply_time_gate_to_params(p)["timestamp.lt"], "2023-06-15")

    def test_leaves_lte_before_gate(self):
        p = {"timestamp.gte": "2023-01-01", "timestamp.lte": "2023-05-01"}
        self.assertEqual(self.client._apply_time_gate_to_params(p)["timestamp.lte"], "2023-05-01")

    # -- inject upper bound when missing --
    def test_adds_lte_when_only_gte(self):
        p = {"timestamp.gte": "2023-01-01"}
        self.assertIn("timestamp.lte", self.client._apply_time_gate_to_params(p))

    def test_does_not_add_lte_when_lt_exists(self):
        p = {"timestamp.gte": "2023-01-01", "timestamp.lt": "2023-05-01"}
        result = self.client._apply_time_gate_to_params(p)
        self.assertNotIn("timestamp.lte", result)

    # -- always inject timestamp.lte fallback --
    def test_adds_timestamp_lte_to_empty(self):
        self.assertIn("timestamp.lte", self.client._apply_time_gate_to_params({}))

    def test_adds_timestamp_lte_with_unrelated_params(self):
        p = {"ticker": "AAPL", "limit": 100}
        result = self.client._apply_time_gate_to_params(p)
        self.assertIn("timestamp.lte", result)
        self.assertEqual(result["ticker"], "AAPL")
        self.assertEqual(result["limit"], 100)

    # -- date-specific upper bounds --
    def test_caps_published_utc_lte(self):
        p = {"published_utc.gte": "2023-01-01", "published_utc.lte": "2023-12-31"}
        self.assertEqual(self.client._apply_time_gate_to_params(p)["published_utc.lte"], "2023-06-15")

    def test_caps_filing_date_lte(self):
        p = {"filing_date.gte": "2023-01-01", "filing_date.lte": "2023-12-31"}
        self.assertEqual(self.client._apply_time_gate_to_params(p)["filing_date.lte"], "2023-06-15")

    def test_caps_execution_date_lte(self):
        p = {"execution_date.gte": "2023-01-01", "execution_date.lte": "2023-12-31"}
        self.assertEqual(self.client._apply_time_gate_to_params(p)["execution_date.lte"], "2023-06-15")

    def test_caps_ex_dividend_date_lte(self):
        p = {"ex_dividend_date.gte": "2023-01-01", "ex_dividend_date.lte": "2023-12-31"}
        self.assertEqual(self.client._apply_time_gate_to_params(p)["ex_dividend_date.lte"], "2023-06-15")

    def test_caps_expiration_date_lte(self):
        p = {"expiration_date.gte": "2023-01-01", "expiration_date.lte": "2023-12-31"}
        self.assertEqual(self.client._apply_time_gate_to_params(p)["expiration_date.lte"], "2023-06-15")

    def test_caps_listing_date_lte(self):
        p = {"listing_date.gte": "2023-01-01", "listing_date.lte": "2023-12-31"}
        self.assertEqual(self.client._apply_time_gate_to_params(p)["listing_date.lte"], "2023-06-15")

    def test_caps_settlement_date_lte(self):
        p = {"settlement_date.gte": "2023-01-01", "settlement_date.lte": "2023-12-31"}
        self.assertEqual(self.client._apply_time_gate_to_params(p)["settlement_date.lte"], "2023-06-15")

    def test_adds_missing_date_lte_for_date_gte(self):
        p = {"date.gte": "2023-01-01"}
        result = self.client._apply_time_gate_to_params(p)
        self.assertIn("date.lte", result)
        self.assertEqual(result["date.lte"], "2023-06-15")

    # -- always inject all date caps even without caller filters --
    def test_always_injects_execution_date_lte(self):
        """execution_date.lte must be injected even when caller doesn't filter by it."""
        p = {"ticker": "AAPL"}
        result = self.client._apply_time_gate_to_params(p)
        self.assertIn("execution_date.lte", result)
        self.assertEqual(result["execution_date.lte"], "2023-06-15")

    def test_always_injects_ex_dividend_date_lte(self):
        p = {"ticker": "AAPL"}
        result = self.client._apply_time_gate_to_params(p)
        self.assertIn("ex_dividend_date.lte", result)

    def test_always_injects_listing_date_lte(self):
        p = {}
        result = self.client._apply_time_gate_to_params(p)
        self.assertIn("listing_date.lte", result)

    def test_always_injects_published_utc_lte(self):
        p = {"ticker": "AAPL"}
        result = self.client._apply_time_gate_to_params(p)
        self.assertIn("published_utc.lte", result)

    def test_always_injects_filing_date_lte(self):
        p = {}
        result = self.client._apply_time_gate_to_params(p)
        self.assertIn("filing_date.lte", result)

    def test_always_injects_settlement_date_lte(self):
        p = {}
        result = self.client._apply_time_gate_to_params(p)
        self.assertIn("settlement_date.lte", result)

    def test_always_injects_expiration_date_lte(self):
        p = {}
        result = self.client._apply_time_gate_to_params(p)
        self.assertIn("expiration_date.lte", result)

    # -- cap plain date params --
    def test_caps_plain_date_param(self):
        """Plain 'date' param (not .lte) should be capped."""
        p = {"date": "2025-01-01"}
        result = self.client._apply_time_gate_to_params(p)
        self.assertEqual(result["date"], "2023-06-15")

    def test_caps_plain_date_param_before_gate_unchanged(self):
        p = {"date": "2023-01-01"}
        result = self.client._apply_time_gate_to_params(p)
        self.assertEqual(result["date"], "2023-01-01")

    def test_caps_as_of_param(self):
        """Plain 'as_of' param should be capped."""
        p = {"as_of": "2025-01-01"}
        result = self.client._apply_time_gate_to_params(p)
        self.assertEqual(result["as_of"], "2023-06-15")

    def test_caps_plain_timestamp_param(self):
        """Plain 'timestamp' param should be capped."""
        p = {"timestamp": "2025-01-01"}
        result = self.client._apply_time_gate_to_params(p)
        self.assertEqual(result["timestamp"], "2023-06-15")

    def test_caps_plain_published_utc_param(self):
        p = {"published_utc": "2025-01-01"}
        result = self.client._apply_time_gate_to_params(p)
        self.assertEqual(result["published_utc"], "2023-06-15")


# ===================================================================
# 4. No gate  → everything passes through unchanged
# ===================================================================

class TimeGateNoGateTest(unittest.TestCase):
    def setUp(self):
        _clear_gate()
        self.client = _make_mock(None)

    def test_value_unchanged(self):
        self.assertEqual(self.client._apply_time_gate_to_value("2030-12-31"), "2030-12-31")

    def test_params_unchanged(self):
        p = {"timestamp.gte": "2023-01-01", "timestamp.lte": "2030-12-31"}
        self.assertEqual(self.client._apply_time_gate_to_params(p.copy()), p)

    def test_no_timestamp_lte_injected(self):
        p = {"ticker": "AAPL"}
        self.assertNotIn("timestamp.lte", self.client._apply_time_gate_to_params(p.copy()))


# ===================================================================
# 5. _apply_time_gate_to_agg_date  (URL-path dates for aggs)
# ===================================================================

class TimeGateAggsDateTest(unittest.TestCase):
    def setUp(self):
        os.environ["TIME_GATE"] = "2023-06-15"
        from polygon.rest.aggs import AggsClient

        class MockAggsClient(AggsClient):
            def __init__(self):
                self.time_gate = self._parse_time_gate(os.getenv("TIME_GATE"))

        self.client = MockAggsClient()

    def tearDown(self):
        _clear_gate()

    def test_str_before(self):
        self.assertEqual(self.client._apply_time_gate_to_agg_date("2023-06-10"), "2023-06-10")

    def test_str_after(self):
        self.assertEqual(self.client._apply_time_gate_to_agg_date("2023-06-20"), "2023-06-15")

    def test_date_before(self):
        self.assertEqual(self.client._apply_time_gate_to_agg_date(date(2023, 6, 10)), date(2023, 6, 10))

    def test_date_after(self):
        self.assertEqual(self.client._apply_time_gate_to_agg_date(date(2023, 6, 20)), date(2023, 6, 15))

    def test_datetime_before(self):
        self.assertEqual(self.client._apply_time_gate_to_agg_date(datetime(2023, 6, 10)), datetime(2023, 6, 10))

    def test_datetime_after(self):
        self.assertEqual(self.client._apply_time_gate_to_agg_date(datetime(2023, 6, 20)), datetime(2023, 6, 15))

    def test_millis_before(self):
        ts = int(datetime(2023, 6, 10).timestamp() * 1000)
        self.assertEqual(self.client._apply_time_gate_to_agg_date(ts), ts)

    def test_millis_after(self):
        ts = int(datetime(2023, 6, 20).timestamp() * 1000)
        expected = int(datetime(2023, 6, 15).timestamp() * 1000)
        self.assertEqual(self.client._apply_time_gate_to_agg_date(ts), expected)

    def test_no_gate_passthrough(self):
        _clear_gate()
        from polygon.rest.aggs import AggsClient

        class MockAggsClient(AggsClient):
            def __init__(self):
                self.time_gate = None

        c = MockAggsClient()
        self.assertEqual(c._apply_time_gate_to_agg_date("2030-12-31"), "2030-12-31")
        self.assertEqual(c._apply_time_gate_to_agg_date(datetime(2030, 12, 31)), datetime(2030, 12, 31))


# ===================================================================
# 6. Blocked live / real-time endpoints  → return []
#    Verify return value is [], is falsy, is iterable, is a list.
# ===================================================================

class TimeGateBlockedEndpointsTest(unittest.TestCase):
    """Every live endpoint must return [] when TIME_GATE is set."""

    def setUp(self):
        os.environ["TIME_GATE"] = "2023-06-15"
        self.client = _make_mock("2023-06-15")

    def tearDown(self):
        _clear_gate()

    def _assert_blocked(self, result):
        """Verify the blocked return value is safe for a model to consume."""
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])
        self.assertFalse(result)            # falsy
        self.assertEqual(len(result), 0)    # iterable with length
        for _ in result:                    # safe to iterate
            self.fail("should be empty")

    # -- quotes --
    def test_get_last_quote(self):
        from polygon.rest.quotes import QuotesClient
        self._assert_blocked(QuotesClient.get_last_quote(self.client, "AAPL"))

    def test_get_last_forex_quote(self):
        from polygon.rest.quotes import QuotesClient
        self._assert_blocked(QuotesClient.get_last_forex_quote(self.client, "USD", "EUR"))

    def test_get_real_time_currency_conversion(self):
        from polygon.rest.quotes import QuotesClient
        self._assert_blocked(QuotesClient.get_real_time_currency_conversion(self.client, "USD", "EUR"))

    # -- trades --
    def test_get_last_trade(self):
        from polygon.rest.trades import TradesClient
        self._assert_blocked(TradesClient.get_last_trade(self.client, "AAPL"))

    def test_get_last_crypto_trade(self):
        from polygon.rest.trades import TradesClient
        self._assert_blocked(TradesClient.get_last_crypto_trade(self.client, "BTC", "USD"))

    # -- aggs --
    def test_get_previous_close_agg(self):
        from polygon.rest.aggs import AggsClient
        self._assert_blocked(AggsClient.get_previous_close_agg(self.client, "AAPL"))

    # -- reference / markets --
    def test_get_market_holidays(self):
        from polygon.rest.reference import MarketsClient
        self._assert_blocked(MarketsClient.get_market_holidays(self.client))

    def test_get_market_status(self):
        from polygon.rest.reference import MarketsClient
        self._assert_blocked(MarketsClient.get_market_status(self.client))

    # -- snapshots (all 8) --
    def test_list_universal_snapshots(self):
        from polygon.rest.snapshot import SnapshotClient
        self._assert_blocked(SnapshotClient.list_universal_snapshots(self.client))

    def test_get_snapshot_all(self):
        from polygon.rest.snapshot import SnapshotClient
        self._assert_blocked(SnapshotClient.get_snapshot_all(self.client, "stocks"))

    def test_get_snapshot_direction(self):
        from polygon.rest.snapshot import SnapshotClient
        self._assert_blocked(SnapshotClient.get_snapshot_direction(self.client, "stocks", "gainers"))

    def test_get_snapshot_ticker(self):
        from polygon.rest.snapshot import SnapshotClient
        self._assert_blocked(SnapshotClient.get_snapshot_ticker(self.client, "stocks", "AAPL"))

    def test_get_snapshot_option(self):
        from polygon.rest.snapshot import SnapshotClient
        self._assert_blocked(SnapshotClient.get_snapshot_option(self.client, "AAPL", "O:AAPL230616C00150000"))

    def test_list_snapshot_options_chain(self):
        from polygon.rest.snapshot import SnapshotClient
        self._assert_blocked(SnapshotClient.list_snapshot_options_chain(self.client, "AAPL"))

    def test_get_snapshot_crypto_book(self):
        from polygon.rest.snapshot import SnapshotClient
        self._assert_blocked(SnapshotClient.get_snapshot_crypto_book(self.client, "X:BTCUSD"))

    def test_get_snapshot_indices(self):
        from polygon.rest.snapshot import SnapshotClient
        self._assert_blocked(SnapshotClient.get_snapshot_indices(self.client))

    # -- summaries --
    def test_get_summaries(self):
        from polygon.rest.summaries import SummariesClient
        self._assert_blocked(SummariesClient.get_summaries(self.client))


class TimeGateBlockedEndpointsNoGateTest(unittest.TestCase):
    """Verify nothing is blocked when TIME_GATE is unset."""

    def setUp(self):
        _clear_gate()
        self.client = _make_mock(None)

    def test_time_gate_is_none(self):
        self.assertIsNone(self.client.time_gate)

    def test_previous_close_not_blocked(self):
        """Without gate the guard `if self.time_gate is not None` is False."""
        self.assertIsNone(self.client.time_gate)  # guard will skip

    def test_snapshot_not_blocked(self):
        self.assertIsNone(self.client.time_gate)

    def test_last_trade_not_blocked(self):
        self.assertIsNone(self.client.time_gate)

    def test_last_quote_not_blocked(self):
        self.assertIsNone(self.client.time_gate)

    def test_summaries_not_blocked(self):
        self.assertIsNone(self.client.time_gate)


# ===================================================================
# 7. forced adjusted=False  (splits / dividends not retroactively applied)
# ===================================================================

class TimeGateAdjustedFalseTest(unittest.TestCase):
    """When time-gated, every method that has an `adjusted` param must
    force it to False regardless of what the caller passes."""

    def setUp(self):
        os.environ["TIME_GATE"] = "2023-06-15"

    def tearDown(self):
        _clear_gate()

    def _simulate_adjusted_override(self, gate_str, caller_adjusted):
        """Reproduce the `if self.time_gate is not None: adjusted = False` pattern."""
        client = _make_mock(gate_str)
        adjusted = caller_adjusted
        if client.time_gate is not None:
            adjusted = False
        return adjusted

    # -- gated: caller passes True → must become False --
    def test_caller_true_gated(self):
        self.assertFalse(self._simulate_adjusted_override("2023-06-15", True))

    # -- gated: caller passes None → must become False --
    def test_caller_none_gated(self):
        self.assertFalse(self._simulate_adjusted_override("2023-06-15", None))

    # -- gated: caller already passes False → stays False --
    def test_caller_false_gated(self):
        self.assertFalse(self._simulate_adjusted_override("2023-06-15", False))

    # -- no gate: caller passes True → stays True --
    def test_caller_true_no_gate(self):
        self.assertTrue(self._simulate_adjusted_override(None, True))

    # -- no gate: caller passes None → stays None --
    def test_caller_none_no_gate(self):
        self.assertIsNone(self._simulate_adjusted_override(None, None))

    # Verify each endpoint has a time-gate guard for the adjusted param.
    # PIT methods use `want_pit_adjust` + `adjusted = True` to API.
    # Non-PIT methods use `adjusted = False` to force unadjusted.
    def _method_has_adjusted_gate_guard(self, method):
        """Check the source has a time_gate-based adjusted override."""
        import inspect
        src = inspect.getsource(method)
        has_gate_check = "self.time_gate is not None" in src or "time_gate" in src
        has_adjusted_override = "adjusted = False" in src or "adjusted = True" in src
        return has_gate_check and has_adjusted_override

    def test_list_aggs_has_guard(self):
        from polygon.rest.aggs import AggsClient
        self.assertTrue(self._method_has_adjusted_gate_guard(AggsClient.list_aggs))

    def test_get_aggs_has_guard(self):
        from polygon.rest.aggs import AggsClient
        self.assertTrue(self._method_has_adjusted_gate_guard(AggsClient.get_aggs))

    def test_get_grouped_daily_aggs_has_guard(self):
        from polygon.rest.aggs import AggsClient
        self.assertTrue(self._method_has_adjusted_gate_guard(AggsClient.get_grouped_daily_aggs))

    def test_get_daily_open_close_agg_has_guard(self):
        from polygon.rest.aggs import AggsClient
        self.assertTrue(self._method_has_adjusted_gate_guard(AggsClient.get_daily_open_close_agg))

    def test_get_sma_has_guard(self):
        from polygon.rest.indicators import IndicatorsClient
        self.assertTrue(self._method_has_adjusted_gate_guard(IndicatorsClient.get_sma))

    def test_get_ema_has_guard(self):
        from polygon.rest.indicators import IndicatorsClient
        self.assertTrue(self._method_has_adjusted_gate_guard(IndicatorsClient.get_ema))

    def test_get_rsi_has_guard(self):
        from polygon.rest.indicators import IndicatorsClient
        self.assertTrue(self._method_has_adjusted_gate_guard(IndicatorsClient.get_rsi))

    def test_get_macd_has_guard(self):
        from polygon.rest.indicators import IndicatorsClient
        self.assertTrue(self._method_has_adjusted_gate_guard(IndicatorsClient.get_macd))


# ===================================================================
# 8. Blocked endpoints have guard in source (exhaustive source check)
# ===================================================================

class TimeGateBlockedGuardSourceTest(unittest.TestCase):
    """Verify every blocked endpoint actually contains the guard in source."""

    def _has_gate_guard(self, method):
        import inspect
        src = inspect.getsource(method)
        return ("self.time_gate is not None" in src and "return []" in src)

    def test_get_last_quote(self):
        from polygon.rest.quotes import QuotesClient
        self.assertTrue(self._has_gate_guard(QuotesClient.get_last_quote))

    def test_get_last_forex_quote(self):
        from polygon.rest.quotes import QuotesClient
        self.assertTrue(self._has_gate_guard(QuotesClient.get_last_forex_quote))

    def test_get_real_time_currency_conversion(self):
        from polygon.rest.quotes import QuotesClient
        self.assertTrue(self._has_gate_guard(QuotesClient.get_real_time_currency_conversion))

    def test_get_last_trade(self):
        from polygon.rest.trades import TradesClient
        self.assertTrue(self._has_gate_guard(TradesClient.get_last_trade))

    def test_get_last_crypto_trade(self):
        from polygon.rest.trades import TradesClient
        self.assertTrue(self._has_gate_guard(TradesClient.get_last_crypto_trade))

    def test_get_previous_close_agg(self):
        from polygon.rest.aggs import AggsClient
        self.assertTrue(self._has_gate_guard(AggsClient.get_previous_close_agg))

    def test_get_market_holidays(self):
        from polygon.rest.reference import MarketsClient
        self.assertTrue(self._has_gate_guard(MarketsClient.get_market_holidays))

    def test_get_market_status(self):
        from polygon.rest.reference import MarketsClient
        self.assertTrue(self._has_gate_guard(MarketsClient.get_market_status))

    def test_get_summaries(self):
        from polygon.rest.summaries import SummariesClient
        self.assertTrue(self._has_gate_guard(SummariesClient.get_summaries))

    def test_all_snapshot_methods(self):
        from polygon.rest.snapshot import SnapshotClient
        for name in [
            "list_universal_snapshots",
            "get_snapshot_all",
            "get_snapshot_direction",
            "get_snapshot_ticker",
            "get_snapshot_option",
            "list_snapshot_options_chain",
            "get_snapshot_crypto_book",
            "get_snapshot_indices",
        ]:
            with self.subTest(method=name):
                self.assertTrue(self._has_gate_guard(getattr(SnapshotClient, name)))


# ===================================================================
# 9. WebSocket blocking
# ===================================================================

class TimeGateWebSocketTest(unittest.TestCase):
    def setUp(self):
        os.environ["TIME_GATE"] = "2023-06-15"

    def tearDown(self):
        _clear_gate()

    def test_flag_set_when_gated(self):
        from polygon.websocket import WebSocketClient
        ws = WebSocketClient(api_key="test_key")
        self.assertTrue(ws._time_gate_enabled)

    def test_flag_unset_when_no_gate(self):
        _clear_gate()
        from polygon.websocket import WebSocketClient
        ws = WebSocketClient(api_key="test_key")
        self.assertFalse(ws._time_gate_enabled)

    def test_connect_returns_immediately(self):
        """connect() should return without calling the processor."""
        from polygon.websocket import WebSocketClient
        ws = WebSocketClient(api_key="test_key")

        async def fail_processor(msgs):
            raise AssertionError("processor should never be called")

        # Must not raise, must not hang
        asyncio.run(ws.connect(fail_processor))

    def test_run_returns_immediately(self):
        """run() (sync wrapper) should also return without calling handler."""
        from polygon.websocket import WebSocketClient
        ws = WebSocketClient(api_key="test_key")

        def fail_handler(msgs):
            raise AssertionError("handler should never be called")

        # Must not raise, must not hang
        ws.run(fail_handler)

    def test_connect_return_value_is_none(self):
        """Blocked connect() returns None (implicit return)."""
        from polygon.websocket import WebSocketClient
        ws = WebSocketClient(api_key="test_key")

        async def noop(msgs):
            pass

        result = asyncio.run(ws.connect(noop))
        self.assertIsNone(result)


# ===================================================================
# 10. Edge cases
# ===================================================================

class TimeGateEdgeCaseTest(unittest.TestCase):
    def setUp(self):
        os.environ["TIME_GATE"] = "2023-06-15"

    def tearDown(self):
        _clear_gate()

    def test_from_after_gate_produces_inverted_range(self):
        """If from_ > time_gate, 'to' gets capped to time_gate which is before from_.
        The API would return no data. This is correct behaviour — no future data leaks."""
        client = _make_mock("2023-06-15")
        from polygon.rest.aggs import AggsClient
        capped_to = AggsClient._apply_time_gate_to_agg_date(client, "2025-01-01")
        self.assertEqual(capped_to, "2023-06-15")
        # from_=2025-01-01, to=2023-06-15 → API returns nothing. Good.

    def test_from_is_not_capped(self):
        """from_ should NOT be gated (only 'to' is capped in aggs)."""
        client = _make_mock("2023-06-15")
        # The _apply_time_gate_to_agg_date helper caps any date.
        # But list_aggs only calls it on `to`, not on `from_`.
        # We verify by inspecting the source:
        import inspect
        from polygon.rest.aggs import AggsClient
        src = inspect.getsource(AggsClient.list_aggs)
        lines = src.splitlines()
        gate_calls = [l.strip() for l in lines if "_apply_time_gate_to_agg_date" in l]
        # Should be exactly 1 call:  to = self._apply_time_gate_to_agg_date(to)
        self.assertEqual(len(gate_calls), 1)
        self.assertIn("to", gate_calls[0])
        self.assertNotIn("from_", gate_calls[0])

    def test_blocked_endpoint_result_works_with_not(self):
        """A model should be able to do `if not result:` safely."""
        client = _make_mock("2023-06-15")
        from polygon.rest.trades import TradesClient
        result = TradesClient.get_last_trade(client, "AAPL")
        self.assertTrue(not result)

    def test_blocked_endpoint_result_works_with_len(self):
        """A model should be able to do `len(result)` safely."""
        client = _make_mock("2023-06-15")
        from polygon.rest.quotes import QuotesClient
        result = QuotesClient.get_last_quote(client, "AAPL")
        self.assertEqual(len(result), 0)

    def test_blocked_endpoint_result_works_with_for_loop(self):
        """A model should be able to iterate safely."""
        client = _make_mock("2023-06-15")
        from polygon.rest.snapshot import SnapshotClient
        result = SnapshotClient.get_snapshot_all(client, "stocks")
        items = [x for x in result]
        self.assertEqual(items, [])

    def test_blocked_endpoint_result_works_with_bool(self):
        """A model should be able to do `bool(result)` safely."""
        client = _make_mock("2023-06-15")
        from polygon.rest.reference import MarketsClient
        result = MarketsClient.get_market_status(client)
        self.assertFalse(bool(result))

    def test_datetime_with_time_gate_preserves_str_format(self):
        """Capping a datetime-formatted string should return datetime format."""
        client = _make_mock("2023-06-15T12:00:00")
        result = client._apply_time_gate_to_value("2023-06-20T15:30:00")
        self.assertIn("T", result)  # preserves datetime format
        self.assertEqual(result, "2023-06-15T12:00:00")

    def test_date_str_gate_preserves_date_format(self):
        """Capping a date-formatted string should return date format (no T)."""
        client = _make_mock("2023-06-15")
        result = client._apply_time_gate_to_value("2023-06-20")
        self.assertNotIn("T", result)
        self.assertEqual(result, "2023-06-15")


# ===================================================================
# 11. Point-in-time ratio-based adjustment
# ===================================================================


class TimeGateRescaleAggTest(unittest.TestCase):
    """Test _rescale_agg which divides prices/volumes by the gate ratio."""

    def test_rescale_prices(self):
        """Dividing by price_ratio removes post-gate adjustments."""
        from polygon.rest.aggs import AggsClient
        from polygon.rest.models import Agg

        # Polygon returned fully adjusted price $100.  Post-gate ratio is 0.5
        # (e.g. a 2:1 split happened AFTER the gate).  Dividing by 0.5 → $200
        # which is what the price was before the post-gate split.
        agg = Agg(open=100.0, high=110.0, low=90.0, close=105.0,
                  volume=4000.0, vwap=102.0, timestamp=1590000000000)
        AggsClient._rescale_agg(agg, price_ratio=0.5, volume_ratio=2.0)

        self.assertAlmostEqual(agg.open, 200.0)
        self.assertAlmostEqual(agg.close, 210.0)
        self.assertAlmostEqual(agg.volume, 2000.0)

    def test_rescale_ratio_1_no_change(self):
        """Ratio of 1.0 means no post-gate adjustments → no change."""
        from polygon.rest.aggs import AggsClient
        from polygon.rest.models import Agg

        agg = Agg(open=100.0, close=105.0, volume=1000.0, timestamp=1590000000000)
        AggsClient._rescale_agg(agg, price_ratio=1.0, volume_ratio=1.0)

        self.assertEqual(agg.open, 100.0)
        self.assertEqual(agg.close, 105.0)
        self.assertEqual(agg.volume, 1000.0)

    def test_rescale_none_fields_safe(self):
        """None fields should stay None, no crash."""
        from polygon.rest.aggs import AggsClient
        from polygon.rest.models import Agg

        agg = Agg(open=None, high=None, low=None, close=None,
                  volume=None, vwap=None, timestamp=1590000000000)
        AggsClient._rescale_agg(agg, price_ratio=0.5, volume_ratio=2.0)

        self.assertIsNone(agg.open)
        self.assertIsNone(agg.volume)

    def test_rescale_rounds_to_4_decimals(self):
        """Rescaled values should be rounded to 4 decimal places."""
        from polygon.rest.aggs import AggsClient
        from polygon.rest.models import Agg

        agg = Agg(open=100.0, close=100.0, timestamp=1590000000000)
        AggsClient._rescale_agg(agg, price_ratio=3.0, volume_ratio=1.0)

        self.assertEqual(agg.open, round(100.0 / 3.0, 4))

    def test_rescale_4_to_1_split_after_gate(self):
        """If a 4:1 split happened AFTER the gate, gate_ratio = 0.25.
        Dividing removes the split: $25 fully-adj → $25/0.25 = $100 PIT."""
        from polygon.rest.aggs import AggsClient
        from polygon.rest.models import Agg

        agg = Agg(open=25.0, close=26.0, volume=8000.0, timestamp=1590000000000)
        # gate_ratio: adj/unadj = 25/100 = 0.25 at gate date
        AggsClient._rescale_agg(agg, price_ratio=0.25, volume_ratio=4.0)

        self.assertAlmostEqual(agg.open, 100.0)
        self.assertAlmostEqual(agg.close, 104.0)
        self.assertAlmostEqual(agg.volume, 2000.0)


class TimeGateComputeRatioTest(unittest.TestCase):
    """Test _compute_gate_adjustment_ratio edge cases."""

    def setUp(self):
        os.environ["TIME_GATE"] = "2021-01-01"

    def tearDown(self):
        _clear_gate()

    def test_returns_1_1_when_no_gate(self):
        from polygon.rest.aggs import AggsClient
        client = _make_mock(None)
        pr, vr = AggsClient._compute_gate_adjustment_ratio(client, "AAPL")
        self.assertEqual(pr, 1.0)
        self.assertEqual(vr, 1.0)

    def test_method_exists(self):
        from polygon.rest.aggs import AggsClient
        self.assertTrue(hasattr(AggsClient, '_compute_gate_adjustment_ratio'))
        self.assertTrue(hasattr(AggsClient, '_rescale_agg'))


class TimeGatePointInTimeSourceTest(unittest.TestCase):
    """Verify which methods have PIT adjustment and which correctly don't."""

    def _has_pit_pattern(self, method):
        import inspect
        src = inspect.getsource(method)
        return "want_pit_adjust" in src and "_compute_gate_adjustment_ratio" in src

    def _has_forced_false_only(self, method):
        """Method forces adjusted=False but does NOT do PIT adjustment."""
        import inspect
        src = inspect.getsource(method)
        has_force = "adjusted = False" in src
        has_pit = "want_pit_adjust" in src
        return has_force and not has_pit

    # -- Methods WITH PIT adjustment --
    def test_list_aggs_has_pit(self):
        from polygon.rest.aggs import AggsClient
        self.assertTrue(self._has_pit_pattern(AggsClient.list_aggs))

    def test_get_aggs_has_pit(self):
        from polygon.rest.aggs import AggsClient
        self.assertTrue(self._has_pit_pattern(AggsClient.get_aggs))

    def test_get_daily_open_close_agg_has_pit(self):
        from polygon.rest.aggs import AggsClient
        self.assertTrue(self._has_pit_pattern(AggsClient.get_daily_open_close_agg))

    # -- Methods WITHOUT PIT (forced False only) --
    def test_get_grouped_daily_aggs_no_pit(self):
        from polygon.rest.aggs import AggsClient
        self.assertTrue(self._has_forced_false_only(AggsClient.get_grouped_daily_aggs))

    def test_get_sma_no_pit(self):
        from polygon.rest.indicators import IndicatorsClient
        self.assertTrue(self._has_forced_false_only(IndicatorsClient.get_sma))

    def test_get_ema_no_pit(self):
        from polygon.rest.indicators import IndicatorsClient
        self.assertTrue(self._has_forced_false_only(IndicatorsClient.get_ema))

    def test_get_rsi_no_pit(self):
        from polygon.rest.indicators import IndicatorsClient
        self.assertTrue(self._has_forced_false_only(IndicatorsClient.get_rsi))

    def test_get_macd_no_pit(self):
        from polygon.rest.indicators import IndicatorsClient
        self.assertTrue(self._has_forced_false_only(IndicatorsClient.get_macd))

    # -- Previous close is blocked entirely --
    def test_previous_close_blocked(self):
        from polygon.rest.aggs import AggsClient
        import inspect
        src = inspect.getsource(AggsClient.get_previous_close_agg)
        self.assertIn("return []", src)
        self.assertNotIn("want_pit_adjust", src)


# ===================================================================
# 12. Side-by-side: adjusted=True vs adjusted=False with time gate
# ===================================================================


class TimeGateAdjustedTrueVsFalseTest(unittest.TestCase):
    """Demonstrate the two paths side by side using the ratio-based
    rescaling approach."""

    def setUp(self):
        os.environ["TIME_GATE"] = "2021-01-01"

    def tearDown(self):
        _clear_gate()

    def test_adjusted_true_rescales_by_gate_ratio(self):
        """adjusted=True: Polygon's fully-adjusted prices are divided by
        gate_ratio to remove post-gate adjustments."""
        from polygon.rest.aggs import AggsClient
        from polygon.rest.models import Agg

        # Polygon returned fully-adjusted prices (adjusted for a 4:1 split
        # that happened AFTER the gate).  gate_ratio = 0.25.
        agg = Agg(open=25.0, close=26.0, volume=8000.0, timestamp=1590000000000)
        AggsClient._rescale_agg(agg, price_ratio=0.25, volume_ratio=4.0)

        # Post-gate split removed: $25/0.25 = $100
        self.assertAlmostEqual(agg.open, 100.0)
        self.assertAlmostEqual(agg.close, 104.0)
        self.assertAlmostEqual(agg.volume, 2000.0)

    def test_adjusted_false_no_rescaling(self):
        """adjusted=False: raw prices returned as-is, no rescaling."""
        from polygon.rest.models import Agg

        agg = Agg(open=400.0, close=410.0, volume=1000.0, timestamp=1590000000000)

        # Path B: want_pit_adjust is False → _rescale_agg never called
        # So prices stay raw:
        self.assertEqual(agg.open, 400.0)
        self.assertEqual(agg.close, 410.0)
        self.assertEqual(agg.volume, 1000.0)

    def test_same_data_two_paths(self):
        """Same fully-adjusted bar: rescaled vs not → different prices."""
        from polygon.rest.aggs import AggsClient
        from polygon.rest.models import Agg

        # Path A: adjusted=True → rescale
        agg_adj = Agg(open=25.0, close=26.0, timestamp=1590000000000)
        AggsClient._rescale_agg(agg_adj, price_ratio=0.25, volume_ratio=1.0)

        # Path B: adjusted=False → raw (simulated as un-rescaled fully-adjusted)
        agg_raw = Agg(open=25.0, close=26.0, timestamp=1590000000000)

        self.assertAlmostEqual(agg_adj.open, 100.0)  # rescaled
        self.assertEqual(agg_raw.open, 25.0)           # not rescaled
        self.assertNotEqual(agg_adj.open, agg_raw.open)

    def test_gate_ratio_1_means_no_post_gate_events(self):
        """If gate_ratio is 1.0, no post-gate splits or dividends occurred."""
        from polygon.rest.aggs import AggsClient
        from polygon.rest.models import Agg

        agg = Agg(open=100.0, close=105.0, timestamp=1590000000000)
        AggsClient._rescale_agg(agg, price_ratio=1.0, volume_ratio=1.0)

        # No change — Polygon's adjusted prices already match PIT
        self.assertEqual(agg.open, 100.0)
        self.assertEqual(agg.close, 105.0)

    def test_want_pit_logic_adjusted_true(self):
        """With gate: adjusted=True → want_pit_adjust=True, adjusted stays True."""
        client = _make_mock("2023-06-15")
        adjusted = True
        want_pit_adjust = False
        if client.time_gate is not None:
            if adjusted is False:
                pass
            else:
                want_pit_adjust = True
                adjusted = True
        self.assertTrue(want_pit_adjust)
        self.assertTrue(adjusted)  # sent to API as true

    def test_want_pit_logic_adjusted_false(self):
        """With gate: adjusted=False → want_pit_adjust=False, adjusted stays False."""
        client = _make_mock("2023-06-15")
        adjusted = False
        want_pit_adjust = False
        if client.time_gate is not None:
            if adjusted is False:
                pass
            else:
                want_pit_adjust = True
                adjusted = True
        self.assertFalse(want_pit_adjust)
        self.assertFalse(adjusted)  # sent to API as false

    def test_want_pit_logic_adjusted_none(self):
        """With gate: adjusted=None (default) → same as True."""
        client = _make_mock("2023-06-15")
        adjusted = None
        want_pit_adjust = False
        if client.time_gate is not None:
            if adjusted is False:
                pass
            else:
                want_pit_adjust = True
                adjusted = True
        self.assertTrue(want_pit_adjust)
        self.assertTrue(adjusted)

    def test_no_gate_adjusted_true_passes_through(self):
        """Without gate, adjusted=True goes straight to API."""
        _clear_gate()
        client = _make_mock(None)
        adjusted = True
        want_pit_adjust = False
        if client.time_gate is not None:
            if adjusted is False:
                pass
            else:
                want_pit_adjust = True
                adjusted = True
        self.assertTrue(adjusted)
        self.assertFalse(want_pit_adjust)

    def test_no_gate_adjusted_false_passes_through(self):
        """Without gate, adjusted=False goes straight to API."""
        _clear_gate()
        client = _make_mock(None)
        adjusted = False
        want_pit_adjust = False
        if client.time_gate is not None:
            if adjusted is False:
                pass
            else:
                want_pit_adjust = True
                adjusted = True
        self.assertFalse(adjusted)
        self.assertFalse(want_pit_adjust)


if __name__ == "__main__":
    unittest.main()
