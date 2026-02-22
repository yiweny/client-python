from .base import BaseClient
from typing import Optional, Any, Dict, List, Union, Iterator, Tuple
from .models import Agg, GroupedDailyAgg, DailyOpenCloseAgg, PreviousCloseAgg, Sort
from urllib3 import HTTPResponse
from datetime import datetime, date, timedelta, timezone

from .models.request import RequestOptionBuilder


class AggsClient(BaseClient):
    # ------------------------------------------------------------------
    # Point-in-time adjustment: fetch adjusted + unadjusted at the gate
    # date, compute gate_ratio = adj/unadj, divide out post-gate events.
    # ------------------------------------------------------------------

    def _compute_gate_adjustment_ratio(
        self, ticker: str
    ) -> Tuple[float, float]:
        """Return (price_ratio, volume_ratio) at the gate date.
        Dividing fully-adjusted values by these removes post-gate
        adjustments (splits + dividends). Returns (1.0, 1.0) on failure."""
        if self.time_gate is None:
            return (1.0, 1.0)

        gate_date = self.time_gate.strftime("%Y-%m-%d")
        from_dt = self.time_gate - timedelta(days=10)  # cover weekends/holidays
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
        """Cap agg URL-path dates (from_, to) at the gate.
        Returns self.time_gate (datetime) when capped, preserving
        sub-day precision for millis conversion in list_aggs/get_aggs.
        Date-only inputs use EOD (23:59:59) for comparison since Polygon
        treats them as covering the full day."""
        if self.time_gate is None:
            return value

        value_dt = None
        if isinstance(value, datetime):
            value_dt = value
        elif isinstance(value, date):
            # date → EOD for comparison (Polygon includes full day)
            value_dt = datetime.combine(value, datetime.min.time()).replace(
                hour=23, minute=59, second=59
            )
        elif isinstance(value, int):
            value_dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc).replace(tzinfo=None)
        elif isinstance(value, str):
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"]:
                try:
                    value_dt = datetime.strptime(value, fmt)
                    if fmt == "%Y-%m-%d":
                        # Date-only → EOD for comparison
                        value_dt = value_dt.replace(hour=23, minute=59, second=59)
                    break
                except ValueError:
                    continue

        if value_dt is None:
            return value

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
        to = self._apply_time_gate_to_agg_date(to)

        # PIT adjustment: fetch adjusted=true, rescale to remove post-gate events.
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
        to = self._apply_time_gate_to_agg_date(to)

        # PIT adjustment: fetch adjusted=true, rescale to remove post-gate events.
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
        # Block if date is past the gate (capped → returns datetime).
        date = self._apply_time_gate_to_agg_date(date)
        if self.time_gate is not None and isinstance(date, datetime):
            return []

        # Force unadjusted (PIT not feasible for all-market endpoint).
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
        # Block if date is past the gate (capped → returns datetime).
        date = self._apply_time_gate_to_agg_date(date)
        if self.time_gate is not None and isinstance(date, datetime):
            return []

        # PIT adjustment: fetch adjusted=true, rescale to remove post-gate events.
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
