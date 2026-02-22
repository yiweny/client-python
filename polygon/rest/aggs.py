from .base import BaseClient
from typing import Optional, Any, Dict, List, Union, Iterator, Tuple
from .models import Agg, GroupedDailyAgg, DailyOpenCloseAgg, PreviousCloseAgg, Sort
from urllib3 import HTTPResponse
from datetime import datetime, date, timedelta, timezone

from .models.request import RequestOptionBuilder


class AggsClient(BaseClient):
    # ------------------------------------------------------------------
    # Point-in-time adjustment via ratio rescaling
    #
    # Polygon's adjusted=true reflects ALL splits & dividends up to today.
    # To get "point-in-time adjusted" prices (only events known at the
    # gate date), we:
    #   1. Fetch fully-adjusted prices from the API (adjusted=true).
    #   2. Fetch ONE reference bar at the gate date, both adjusted and
    #      unadjusted, to compute a gate_ratio.
    #   3. Divide all adjusted prices by gate_ratio.
    #
    # gate_ratio captures every post-gate adjustment (splits + dividends).
    # Dividing it out leaves only pre-gate adjustments — exactly what a
    # person querying on the gate date would have seen.
    # ------------------------------------------------------------------

    def _compute_gate_adjustment_ratio(
        self, ticker: str
    ) -> Tuple[float, float]:
        """
        Return (price_ratio, volume_ratio) at the gate date.

        price_ratio  = adjusted_close / unadjusted_close
        volume_ratio = adjusted_volume / unadjusted_volume

        Dividing Polygon's fully-adjusted values by these ratios removes
        every post-gate adjustment (splits + dividends).

        Returns (1.0, 1.0) when no rescaling is needed.
        """
        if self.time_gate is None:
            return (1.0, 1.0)

        gate_date = self.time_gate.strftime("%Y-%m-%d")
        # Look back up to 10 days to find a trading day at/before the gate
        from_dt = self.time_gate - timedelta(days=10)
        from_date = from_dt.strftime("%Y-%m-%d")
        path = f"/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{gate_date}"

        import time

        adj_bars = None
        raw_bars = None
        for attempt in range(3):
            try:
                adj_bars = self._get(
                    path=path,
                    params={"adjusted": "true", "limit": 10, "sort": "desc"},
                    result_key="results",
                    deserializer=Agg.from_dict,
                )
                raw_bars = self._get(
                    path=path,
                    params={"adjusted": "false", "limit": 10, "sort": "desc"},
                    result_key="results",
                    deserializer=Agg.from_dict,
                )
                break
            except Exception:
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                else:
                    return (1.0, 1.0)

        if (
            not adj_bars
            or not raw_bars
            or not isinstance(adj_bars, list)
            or not isinstance(raw_bars, list)
        ):
            return (1.0, 1.0)

        adj = adj_bars[0]  # most recent bar (desc sort)
        raw = raw_bars[0]

        price_ratio = 1.0
        if (
            adj.close is not None
            and raw.close is not None
            and raw.close != 0
        ):
            price_ratio = adj.close / raw.close

        volume_ratio = 1.0
        if (
            adj.volume is not None
            and raw.volume is not None
            and raw.volume != 0
        ):
            volume_ratio = adj.volume / raw.volume

        return (price_ratio, volume_ratio)

    @staticmethod
    def _rescale_agg(
        agg: Agg, price_ratio: float, volume_ratio: float
    ) -> Agg:
        """Divide an Agg's prices by *price_ratio* and volume by *volume_ratio*."""
        if price_ratio != 1.0:
            if agg.open is not None:
                agg.open = round(agg.open / price_ratio, 4)
            if agg.high is not None:
                agg.high = round(agg.high / price_ratio, 4)
            if agg.low is not None:
                agg.low = round(agg.low / price_ratio, 4)
            if agg.close is not None:
                agg.close = round(agg.close / price_ratio, 4)
            if agg.vwap is not None:
                agg.vwap = round(agg.vwap / price_ratio, 4)
        if volume_ratio != 1.0:
            if agg.volume is not None:
                agg.volume = round(agg.volume / volume_ratio, 4)
        return agg

    # ------------------------------------------------------------------

    def _apply_time_gate_to_agg_date(
        self, value: Union[str, int, datetime, date]
    ) -> Union[str, int, datetime, date]:
        """
        Apply time gate to aggregate endpoint date parameters (from_, to).
        These are in the URL path, not query params.

        When the value exceeds the gate, we ALWAYS return self.time_gate
        as a datetime.  This preserves second-level precision so that
        callers like list_aggs/get_aggs can convert to millisecond
        timestamps for the URL (Polygon accepts both date strings and
        ms timestamps).  Callers that need a date string (daily endpoints)
        handle the conversion themselves.
        """
        if self.time_gate is None:
            return value

        # Convert to datetime for comparison
        value_dt = None

        if isinstance(value, datetime):
            value_dt = value
        elif isinstance(value, date):
            value_dt = datetime.combine(value, datetime.min.time())
        elif isinstance(value, int):
            # Unix milliseconds timestamp
            value_dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc).replace(tzinfo=None)
        elif isinstance(value, str):
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"]:
                try:
                    value_dt = datetime.strptime(value, fmt)
                    break
                except ValueError:
                    continue

        if value_dt is None:
            return value

        # Cap at time_gate — always return the gate as a datetime so
        # downstream code can convert to the right precision.
        if value_dt > self.time_gate:
            return self.time_gate

        return value

    def list_aggs(
        self,
        ticker: str,
        multiplier: int,
        timespan: str,
        # "from" is a keyword in python https://www.w3schools.com/python/python_ref_keywords.asp
        from_: Union[str, int, datetime, date],
        to: Union[str, int, datetime, date],
        adjusted: Optional[bool] = None,
        sort: Optional[Union[str, Sort]] = None,
        limit: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
        raw: bool = False,
        options: Optional[RequestOptionBuilder] = None,
    ) -> Union[Iterator[Agg], HTTPResponse]:
        """
        List aggregate bars for a ticker over a given date range in custom time window sizes.

        :param ticker: The ticker symbol.
        :param multiplier: The size of the timespan multiplier.
        :param timespan: The size of the time window.
        :param from_: The start of the aggregate time window as YYYY-MM-DD, a date, Unix MS Timestamp, or a datetime.
        :param to: The end of the aggregate time window as YYYY-MM-DD, a date, Unix MS Timestamp, or a datetime.
        :param adjusted: Whether or not the results are adjusted for splits. By default, results are adjusted. Set this to false to get results that are NOT adjusted for splits.
        :param sort: Sort the results by timestamp. asc will return results in ascending order (oldest at the top), desc will return results in descending order (newest at the top).The end of the aggregate time window.
        :param limit: Limits the number of base aggregates queried to create the aggregate results. Max 50000 and Default 5000. Read more about how limit is used to calculate aggregate results in our article on Aggregate Data API Improvements.
        :param params: Any additional query params
        :param raw: Return raw object instead of results object
        :return: Iterator of aggregates
        """
        # Apply time gate to 'to' parameter
        to = self._apply_time_gate_to_agg_date(to)

        # When time-gated and the caller wants adjusted data, we fetch
        # adjusted=true from Polygon (which adjusts for ALL events up to
        # today) and then rescale to remove post-gate adjustments.
        # When the caller wants unadjusted, we just force adjusted=false.
        want_pit_adjust = False
        if self.time_gate is not None:
            if adjusted is False:
                pass  # caller explicitly wants raw — leave as-is
            else:
                want_pit_adjust = True
                adjusted = True  # fetch fully-adjusted from API

        if isinstance(from_, datetime):
            from_ = int(from_.timestamp() * self.time_mult("millis"))

        if isinstance(to, datetime):
            to = int(to.timestamp() * self.time_mult("millis"))
        url = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_}/{to}"

        result = self._paginate(
            path=url,
            params=self._get_params(self.list_aggs, locals()),
            raw=raw,
            deserializer=Agg.from_dict,
            options=options,
        )

        if want_pit_adjust and not raw:
            pr, vr = self._compute_gate_adjustment_ratio(ticker)
            if pr != 1.0 or vr != 1.0:
                return (self._rescale_agg(a, pr, vr) for a in result)

        return result

    def get_aggs(
        self,
        ticker: str,
        multiplier: int,
        timespan: str,
        # "from" is a keyword in python https://www.w3schools.com/python/python_ref_keywords.asp
        from_: Union[str, int, datetime, date],
        to: Union[str, int, datetime, date],
        adjusted: Optional[bool] = None,
        sort: Optional[Union[str, Sort]] = None,
        limit: Optional[int] = None,
        params: Optional[Dict[str, Any]] = None,
        raw: bool = False,
        options: Optional[RequestOptionBuilder] = None,
    ) -> Union[List[Agg], HTTPResponse]:
        """
        Get aggregate bars for a ticker over a given date range in custom time window sizes.

        :param ticker: The ticker symbol.
        :param multiplier: The size of the timespan multiplier.
        :param timespan: The size of the time window.
        :param from_: The start of the aggregate time window as YYYY-MM-DD, a date, Unix MS Timestamp, or a datetime.
        :param to: The end of the aggregate time window as YYYY-MM-DD, a date, Unix MS Timestamp, or a datetime.
        :param adjusted: Whether or not the results are adjusted for splits. By default, results are adjusted. Set this to false to get results that are NOT adjusted for splits.
        :param sort: Sort the results by timestamp. asc will return results in ascending order (oldest at the top), desc will return results in descending order (newest at the top).The end of the aggregate time window.
        :param limit: Limits the number of base aggregates queried to create the aggregate results. Max 50000 and Default 5000. Read more about how limit is used to calculate aggregate results in our article on Aggregate Data API Improvements.
        :param params: Any additional query params
        :param raw: Return raw object instead of results object
        :return: List of aggregates
        """
        # Apply time gate to 'to' parameter
        to = self._apply_time_gate_to_agg_date(to)

        # When time-gated and the caller wants adjusted data, we fetch
        # adjusted=true from Polygon and then rescale to remove post-gate
        # adjustments. When the caller wants unadjusted, force adjusted=false.
        want_pit_adjust = False
        if self.time_gate is not None:
            if adjusted is False:
                pass
            else:
                want_pit_adjust = True
                adjusted = True

        if isinstance(from_, datetime):
            from_ = int(from_.timestamp() * self.time_mult("millis"))

        if isinstance(to, datetime):
            to = int(to.timestamp() * self.time_mult("millis"))
        url = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_}/{to}"

        result = self._get(
            path=url,
            params=self._get_params(self.get_aggs, locals()),
            result_key="results",
            deserializer=Agg.from_dict,
            raw=raw,
            options=options,
        )

        if want_pit_adjust and not raw and isinstance(result, list):
            pr, vr = self._compute_gate_adjustment_ratio(ticker)
            if pr != 1.0 or vr != 1.0:
                for agg in result:
                    self._rescale_agg(agg, pr, vr)

        return result

    # TODO: next breaking change release move "market_type" to be 2nd mandatory
    # param
    def get_grouped_daily_aggs(
        self,
        date: Union[str, date],
        adjusted: Optional[bool] = None,
        params: Optional[Dict[str, Any]] = None,
        raw: bool = False,
        locale: str = "us",
        market_type: str = "stocks",
        include_otc: bool = False,
        options: Optional[RequestOptionBuilder] = None,
    ) -> Union[List[GroupedDailyAgg], HTTPResponse]:
        """
        Get the daily open, high, low, and close (OHLC) for the entire market.

        :param date: The beginning date for the aggregate window.
        :param adjusted: Whether or not the results are adjusted for splits. By default, results are adjusted. Set this to false to get results that are NOT adjusted for splits.
        :param params: Any additional query params
        :param raw: Return raw object instead of results object
        :return: List of grouped daily aggregates
        """
        # Apply time gate. _apply_time_gate_to_agg_date returns a
        # datetime when the requested date was capped (i.e. it was past
        # the gate).  For this single-date endpoint, silently serving a
        # different day's data would be misleading, so block instead.
        date = self._apply_time_gate_to_agg_date(date)
        if self.time_gate is not None and isinstance(date, datetime):
            return []

        # Force unadjusted data when time-gated.  Point-in-time split
        # adjustment is NOT applied here because grouped daily returns
        # data for the entire market (thousands of tickers) and fetching
        # splits for each would be impractical.
        if self.time_gate is not None:
            adjusted = False

        url = f"/v2/aggs/grouped/locale/{locale}/market/{market_type}/{date}"

        return self._get(
            path=url,
            params=self._get_params(self.get_grouped_daily_aggs, locals()),
            result_key="results",
            deserializer=GroupedDailyAgg.from_dict,
            raw=raw,
            options=options,
        )

    def get_daily_open_close_agg(
        self,
        ticker: str,
        date: Union[str, date],
        adjusted: Optional[bool] = None,
        params: Optional[Dict[str, Any]] = None,
        raw: bool = False,
        options: Optional[RequestOptionBuilder] = None,
    ) -> Union[DailyOpenCloseAgg, HTTPResponse]:
        """
        Get the open, close and afterhours prices of a stock symbol on a certain date.

        :param ticker: The exchange symbol that this item is traded under.
        :param date: The beginning date for the aggregate window.
        :param adjusted: Whether or not the results are adjusted for splits. By default, results are adjusted. Set this to false to get results that are NOT adjusted for splits.
        :param params: Any additional query params
        :param raw: Return raw object instead of results object
        :return: Daily open close aggregate
        """
        # Apply time gate.  If the requested date was past the gate,
        # block — silently serving a different day's data is misleading.
        date = self._apply_time_gate_to_agg_date(date)
        if self.time_gate is not None and isinstance(date, datetime):
            return []

        # When time-gated and the caller wants adjusted data, we fetch
        # adjusted=true and rescale. Otherwise force adjusted=false.
        want_pit_adjust = False
        if self.time_gate is not None:
            if adjusted is False:
                pass
            else:
                want_pit_adjust = True
                adjusted = True

        url = f"/v1/open-close/{ticker}/{date}"

        result = self._get(
            path=url,
            params=self._get_params(self.get_daily_open_close_agg, locals()),
            deserializer=DailyOpenCloseAgg.from_dict,
            raw=raw,
            options=options,
        )

        if (
            want_pit_adjust
            and not raw
            and isinstance(result, DailyOpenCloseAgg)
        ):
            pr, _ = self._compute_gate_adjustment_ratio(ticker)
            if pr != 1.0:
                for field in ("open", "high", "low", "close",
                              "after_hours", "pre_market"):
                    val = getattr(result, field, None)
                    if val is not None:
                        setattr(result, field, round(val / pr, 4))

        return result

    def get_previous_close_agg(
        self,
        ticker: str,
        adjusted: Optional[bool] = None,
        params: Optional[Dict[str, Any]] = None,
        raw: bool = False,
        options: Optional[RequestOptionBuilder] = None,
    ) -> Union[PreviousCloseAgg, HTTPResponse]:
        """
        Get the previous day's open, high, low, and close (OHLC) for the specified stock ticker.

        :param ticker: The ticker symbol of the stock/equity.
        :param adjusted: Whether or not the results are adjusted for splits. By default, results are adjusted. Set this to false to get results that are NOT adjusted for splits.
        :param params: Any additional query params
        :param raw: Return raw object instead of results object
        :return: Previous close aggregate
        """
        # Block when time-gating is enabled since this endpoint always
        # returns the most recent previous day's data (live).
        if self.time_gate is not None:
            return []

        url = f"/v2/aggs/ticker/{ticker}/prev"

        return self._get(
            path=url,
            params=self._get_params(self.get_previous_close_agg, locals()),
            result_key="results",
            deserializer=PreviousCloseAgg.from_dict,
            raw=raw,
            options=options,
        )
