from .base import BaseClient
from typing import Optional, Any, Dict, List, Union, Iterator, Tuple
from .models import Agg, GroupedDailyAgg, DailyOpenCloseAgg, PreviousCloseAgg, Sort
from .models.splits import Split
from urllib3 import HTTPResponse
from datetime import datetime, date

from .models.request import RequestOptionBuilder


class AggsClient(BaseClient):
    # ------------------------------------------------------------------
    # Point-in-time split adjustment helpers
    # ------------------------------------------------------------------

    def _fetch_splits_before_gate(
        self, ticker: str
    ) -> List[Tuple[int, float, float]]:
        """
        Fetch all stock splits for *ticker* with execution_date <= time_gate.

        Returns a sorted list of (execution_timestamp_ms, price_factor,
        volume_factor) tuples.  price_factor = split_from / split_to
        (e.g. 0.25 for a 4-for-1 split).
        """
        if self.time_gate is None:
            return []

        gate_date = self.time_gate.strftime("%Y-%m-%d")
        try:
            raw_splits = self._get(
                path="/v3/reference/splits",
                params={
                    "ticker": ticker,
                    "execution_date.lte": gate_date,
                    "limit": 1000,
                    "order": "asc",
                    "sort": "execution_date",
                },
                result_key="results",
                deserializer=Split.from_dict,
            )
        except Exception:
            return []

        if not raw_splits or not isinstance(raw_splits, list):
            return []

        events: List[Tuple[int, float, float]] = []
        for s in raw_splits:
            if (
                s.execution_date
                and s.split_from
                and s.split_to
                and s.split_to != 0
            ):
                try:
                    exec_dt = datetime.strptime(s.execution_date, "%Y-%m-%d")
                    exec_ts_ms = int(exec_dt.timestamp() * 1000)
                    price_factor = s.split_from / s.split_to
                    volume_factor = s.split_to / s.split_from
                    events.append((exec_ts_ms, price_factor, volume_factor))
                except (ValueError, ZeroDivisionError):
                    continue

        events.sort(key=lambda x: x[0])
        return events

    @staticmethod
    def _adjust_agg(
        agg: Agg, split_events: List[Tuple[int, float, float]]
    ) -> Agg:
        """
        Apply point-in-time split adjustments to a single Agg bar.

        For each split whose execution_date is *after* this bar's timestamp
        (i.e. the split hadn't happened yet when this bar was recorded),
        multiply prices by split_from/split_to and volume by split_to/split_from.
        """
        if not split_events or agg.timestamp is None:
            return agg

        cum_price = 1.0
        cum_vol = 1.0
        for exec_ts_ms, pf, vf in split_events:
            if exec_ts_ms > agg.timestamp:
                cum_price *= pf
                cum_vol *= vf

        if cum_price != 1.0:
            if agg.open is not None:
                agg.open = round(agg.open * cum_price, 4)
            if agg.high is not None:
                agg.high = round(agg.high * cum_price, 4)
            if agg.low is not None:
                agg.low = round(agg.low * cum_price, 4)
            if agg.close is not None:
                agg.close = round(agg.close * cum_price, 4)
            if agg.vwap is not None:
                agg.vwap = round(agg.vwap * cum_price, 4)
            if agg.volume is not None:
                agg.volume = round(agg.volume * cum_vol, 4)

        return agg

    # ------------------------------------------------------------------

    def _apply_time_gate_to_agg_date(
        self, value: Union[str, int, datetime, date]
    ) -> Union[str, int, datetime, date]:
        """
        Apply time gate to aggregate endpoint date parameters (from_, to).
        These are in the URL path, not query params.
        """
        if self.time_gate is None:
            return value

        # Convert to datetime for comparison
        value_dt = None
        original_type = type(value)

        if isinstance(value, datetime):
            value_dt = value
        elif isinstance(value, date):
            value_dt = datetime.combine(value, datetime.min.time())
        elif isinstance(value, int):
            # Unix milliseconds timestamp
            value_dt = datetime.fromtimestamp(value / 1000)
        elif isinstance(value, str):
            # Try to parse as date string
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"]:
                try:
                    value_dt = datetime.strptime(value, fmt)
                    break
                except ValueError:
                    continue

        if value_dt is None:
            return value

        # Cap at time_gate
        if value_dt > self.time_gate:
            if original_type == datetime:
                return self.time_gate
            elif original_type == date:
                return self.time_gate.date()
            elif original_type == int:
                return int(self.time_gate.timestamp() * 1000)
            elif original_type == str:
                if "T" in value:
                    return self.time_gate.strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    return self.time_gate.strftime("%Y-%m-%d")

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

        # When time-gated: always fetch unadjusted from the API.
        # If the caller wanted adjusted=True, we apply point-in-time
        # split adjustments ourselves (only splits known at the gate).
        want_pit_adjust = False
        if self.time_gate is not None:
            want_pit_adjust = adjusted is None or adjusted is True
            adjusted = False

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
            split_events = self._fetch_splits_before_gate(ticker)
            if split_events:
                return (self._adjust_agg(a, split_events) for a in result)

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

        # When time-gated: always fetch unadjusted from the API.
        # If the caller wanted adjusted=True, we apply point-in-time
        # split adjustments ourselves (only splits known at the gate).
        want_pit_adjust = False
        if self.time_gate is not None:
            want_pit_adjust = adjusted is None or adjusted is True
            adjusted = False

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
            split_events = self._fetch_splits_before_gate(ticker)
            if split_events:
                for agg in result:
                    self._adjust_agg(agg, split_events)

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
        # Apply time gate to date parameter
        date = self._apply_time_gate_to_agg_date(date)

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
        # Apply time gate to date parameter
        date = self._apply_time_gate_to_agg_date(date)

        # When time-gated: always fetch unadjusted from the API.
        # If the caller wanted adjusted=True, we apply point-in-time
        # split adjustments ourselves (only splits known at the gate).
        want_pit_adjust = False
        if self.time_gate is not None:
            want_pit_adjust = adjusted is None or adjusted is True
            adjusted = False

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
            split_events = self._fetch_splits_before_gate(ticker)
            if split_events:
                # DailyOpenCloseAgg doesn't have a millisecond timestamp;
                # parse the from_ date string to get one for comparison.
                bar_dt = None
                if result.from_:
                    try:
                        bar_dt = datetime.strptime(result.from_, "%Y-%m-%d")
                    except ValueError:
                        pass
                if bar_dt is not None:
                    bar_ts = int(bar_dt.timestamp() * 1000)
                    cum_price = 1.0
                    for exec_ts_ms, pf, _ in split_events:
                        if exec_ts_ms > bar_ts:
                            cum_price *= pf
                    if cum_price != 1.0:
                        if result.open is not None:
                            result.open = round(result.open * cum_price, 4)
                        if result.high is not None:
                            result.high = round(result.high * cum_price, 4)
                        if result.low is not None:
                            result.low = round(result.low * cum_price, 4)
                        if result.close is not None:
                            result.close = round(result.close * cum_price, 4)
                        if result.after_hours is not None:
                            result.after_hours = round(
                                result.after_hours * cum_price, 4
                            )
                        if result.pre_market is not None:
                            result.pre_market = round(
                                result.pre_market * cum_price, 4
                            )

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
