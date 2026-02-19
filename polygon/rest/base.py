import certifi
import json
import urllib3
import inspect
import os
from urllib3.util.retry import Retry
from enum import Enum
from typing import Optional, Any, Dict, Union
from datetime import datetime, date
from importlib.metadata import version, PackageNotFoundError
from .models.request import RequestOptionBuilder
from ..logging import get_logger
import logging
from urllib.parse import urlencode, urlparse
from ..exceptions import AuthError, BadResponse

TIME_GATE_ENV = "TIME_GATE"

logger = get_logger("RESTClient")
version_number = "unknown"
try:
    version_number = version("polygon-api-client")
except PackageNotFoundError:
    pass


class BaseClient:
    def __init__(
        self,
        api_key: Optional[str],
        connect_timeout: float,
        read_timeout: float,
        num_pools: int,
        retries: int,
        base: str,
        verbose: bool,
        trace: bool,
        custom_json: Optional[Any] = None,
    ):
        if api_key is None:
            raise AuthError(
                f"Must specify env var POLYGON_API_KEY or pass api_key in constructor"
            )

        self.API_KEY = api_key
        self.BASE = base

        # Parse TIME_GATE environment variable
        self.time_gate = self._parse_time_gate(os.getenv(TIME_GATE_ENV))

        self.headers = {
            "Authorization": "Bearer " + self.API_KEY,
            "Accept-Encoding": "gzip",
            "User-Agent": f"Polygon.io PythonClient/{version_number}",
        }

        # initialize self.retries with the parameter value before using it
        self.retries = retries

        # https://urllib3.readthedocs.io/en/stable/reference/urllib3.util.html#urllib3.util.Retry.RETRY_AFTER_STATUS_CODES
        retry_strategy = Retry(
            total=self.retries,
            status_forcelist=[
                413,
                429,
                499,
                500,
                502,
                503,
                504,
            ],  # default 413, 429, 503
            backoff_factor=0.1,  # [0.0s, 0.2s, 0.4s, 0.8s, 1.6s, ...]
        )

        # https://urllib3.readthedocs.io/en/stable/reference/urllib3.poolmanager.html
        # https://urllib3.readthedocs.io/en/stable/reference/urllib3.connectionpool.html#urllib3.HTTPConnectionPool
        self.client = urllib3.PoolManager(
            num_pools=num_pools,
            headers=self.headers,  # default headers sent with each request.
            ca_certs=certifi.where(),
            cert_reqs="CERT_REQUIRED",
            retries=retry_strategy,  # use the customized Retry instance
        )

        self.timeout = urllib3.Timeout(connect=connect_timeout, read=read_timeout)

        if verbose:
            logger.setLevel(logging.DEBUG)
        self.trace = trace
        if custom_json:
            self.json = custom_json
        else:
            self.json = json

    @staticmethod
    def _parse_time_gate(time_gate_str: Optional[str]) -> Optional[datetime]:
        """
        Parse the TIME_GATE environment variable value.
        Supports:
        - ISO date string: "2023-01-01"
        - ISO datetime string: "2023-01-01T12:00:00"
        - Unix timestamp in seconds or milliseconds
        Returns None if the value is empty or None.
        """
        if not time_gate_str:
            return None

        time_gate_str = time_gate_str.strip()
        if not time_gate_str:
            return None

        # Try parsing as ISO format first
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"]:
            try:
                return datetime.strptime(time_gate_str, fmt)
            except ValueError:
                continue

        # Try parsing as Unix timestamp
        try:
            ts = int(time_gate_str)
            # If the timestamp is large (> year 2100 in seconds), assume milliseconds
            if ts > 4102444800:  # Year 2100 in seconds
                ts = ts // 1000
            return datetime.fromtimestamp(ts)
        except (ValueError, OSError):
            pass

        logger.warning(f"Could not parse TIME_GATE value: {time_gate_str}")
        return None

    def _decode(self, resp):
        return self.json.loads(resp.data.decode("utf-8"))

    def _get(
        self,
        path: str,
        params: Optional[dict] = None,
        result_key: Optional[str] = None,
        deserializer=None,
        raw: bool = False,
        options: Optional[RequestOptionBuilder] = None,
    ) -> Any:
        option = options if options is not None else RequestOptionBuilder()

        headers = self._concat_headers(option.headers)

        if self.trace:
            full_url = f"{self.BASE}{path}"
            if params:
                full_url += f"?{urlencode(params)}"
            print_headers = headers.copy()
            if "Authorization" in print_headers:
                print_headers["Authorization"] = print_headers["Authorization"].replace(
                    self.API_KEY, "REDACTED"
                )
            print(f"Request URL: {full_url}")
            print(f"Request Headers: {print_headers}")

        resp = self.client.request(
            "GET",
            self.BASE + path,
            fields=params,
            headers=headers,
        )

        if self.trace:
            resp_headers_dict = dict(resp.headers.items())
            print(f"Response Headers: {resp_headers_dict}")

        if resp.status != 200:
            raise BadResponse(resp.data.decode("utf-8"))

        if raw:
            return resp

        try:
            obj = self._decode(resp)
        except ValueError as e:
            print(f"Error decoding json response: {e}")
            return []

        if result_key:
            if result_key not in obj:
                return []
            obj = obj[result_key]

        if deserializer:
            if type(obj) == list:
                obj = [deserializer(o) for o in obj]
            else:
                obj = deserializer(obj)

        return obj

    @staticmethod
    def time_mult(timestamp_res: str) -> int:
        if timestamp_res == "nanos":
            return 1000000000
        elif timestamp_res == "micros":
            return 1000000
        elif timestamp_res == "millis":
            return 1000

        return 1

    def _apply_time_gate_to_value(
        self,
        value: Union[str, int, datetime, date, None],
        datetime_res: str = "nanos",
    ) -> Union[str, int, datetime, date, None]:
        """
        Apply time gate to a single timestamp/date value.
        Returns the minimum of the value and time_gate.
        """
        if self.time_gate is None or value is None:
            return value

        # Convert value to datetime for comparison
        value_dt = None
        if isinstance(value, datetime):
            value_dt = value
        elif isinstance(value, date):
            value_dt = datetime.combine(value, datetime.min.time())
        elif isinstance(value, int):
            # Assume it's a Unix timestamp, normalize to seconds
            divisor = self.time_mult(datetime_res)
            value_dt = datetime.fromtimestamp(value / divisor)
        elif isinstance(value, str):
            # Try to parse as date string
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"]:
                try:
                    value_dt = datetime.strptime(value, fmt)
                    break
                except ValueError:
                    continue

        if value_dt is None:
            # Could not parse, return original value
            return value

        # Return the minimum of value and time_gate
        if value_dt > self.time_gate:
            # Return time_gate in the same format as the original value
            if isinstance(value, datetime):
                return self.time_gate
            elif isinstance(value, date):
                return self.time_gate.date()
            elif isinstance(value, int):
                return int(self.time_gate.timestamp() * self.time_mult(datetime_res))
            elif isinstance(value, str):
                # Return as the same string format
                if "T" in value:
                    return self.time_gate.strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    return self.time_gate.strftime("%Y-%m-%d")

        return value

    def _apply_time_gate_to_params(
        self, params: Dict[str, Any], datetime_res: str = "nanos"
    ) -> Dict[str, Any]:
        """
        Apply time gate to query parameters.
        This caps timestamp.lte, timestamp.lt, and similar date parameters at the time gate.
        If no upper bound is set, adds timestamp.lte = time_gate.
        """
        if self.time_gate is None:
            return params

        # Parameters that represent upper time bounds (less than or equal)
        upper_bound_params = [
            "timestamp.lte",
            "timestamp.lt",
            "published_utc.lte",
            "published_utc.lt",
            "filing_date.lte",
            "filing_date.lt",
            "period_of_report_date.lte",
            "period_of_report_date.lt",
            "execution_date.lte",
            "execution_date.lt",
            "ex_dividend_date.lte",
            "ex_dividend_date.lt",
            "record_date.lte",
            "record_date.lt",
            "declaration_date.lte",
            "declaration_date.lt",
            "pay_date.lte",
            "pay_date.lt",
            "expiration_date.lte",
            "expiration_date.lt",
            "settlement_date.lte",
            "settlement_date.lt",
            "date.lte",
            "date.lt",
            "listing_date.lte",
            "listing_date.lt",
        ]

        # Base names for timestamp parameters (we'll add .lte if no upper bound exists)
        timestamp_base_params = [
            "timestamp",
            "published_utc",
            "filing_date",
            "period_of_report_date",
            "execution_date",
            "ex_dividend_date",
            "record_date",
            "declaration_date",
            "pay_date",
            "expiration_date",
            "settlement_date",
            "date",
            "listing_date",
        ]

        # Cap existing upper bound parameters
        for param in upper_bound_params:
            if param in params:
                params[param] = self._apply_time_gate_to_value(
                    params[param], datetime_res
                )

        # Cap plain date/timestamp params (e.g. "date", "as_of") that are
        # passed as exact values rather than as .lte/.lt range filters.
        plain_date_params = timestamp_base_params + ["as_of"]
        for param in plain_date_params:
            if param in params:
                params[param] = self._apply_time_gate_to_value(
                    params[param], datetime_res
                )

        # For each base param, always inject an upper bound (.lte) if none
        # exists. This ensures the time gate is enforced even when the caller
        # omits date filters entirely (e.g. list_splits(ticker="AAPL")
        # without an execution_date filter).
        for base_param in timestamp_base_params:
            lte_param = f"{base_param}.lte"
            lt_param = f"{base_param}.lt"

            if lte_param not in params and lt_param not in params:
                # Use date format for date-based params, nanos int for timestamp
                if base_param == "timestamp":
                    params[lte_param] = int(
                        self.time_gate.timestamp() * self.time_mult(datetime_res)
                    )
                else:
                    params[lte_param] = self.time_gate.strftime("%Y-%m-%d")

        return params

    def _get_params(
        self, fn, caller_locals: Dict[str, Any], datetime_res: str = "nanos"
    ):
        params = caller_locals["params"]
        if params is None:
            params = {}
        # https://docs.python.org/3.8/library/inspect.html#inspect.Signature
        for argname, v in inspect.signature(fn).parameters.items():
            # https://docs.python.org/3.8/library/inspect.html#inspect.Parameter
            if argname in ["params", "raw"]:
                continue
            if v.default != v.empty:
                # timestamp_lt -> timestamp.lt
                val = caller_locals.get(argname, v.default)
                if isinstance(val, Enum):
                    val = val.value
                elif isinstance(val, bool):
                    val = str(val).lower()
                elif isinstance(val, datetime):
                    val = int(val.timestamp() * self.time_mult(datetime_res))
                if val is not None:
                    for ext in ["lt", "lte", "gt", "gte", "any_of"]:
                        if argname.endswith(f"_{ext}"):
                            # lop off ext, then rebuild argname with ext,
                            # using ., and not _ (removesuffix would work)
                            # but that is python 3.9+
                            argname = argname[: -len(f"_{ext}")] + f".{ext}"
                    if argname.endswith("any_of"):
                        val = ",".join(val)
                    params[argname] = val

        # Apply time gate to parameters
        params = self._apply_time_gate_to_params(params, datetime_res)

        return params

    def _concat_headers(self, headers: Optional[Dict[str, str]]) -> Dict[str, str]:
        if headers is None:
            return self.headers
        return {**headers, **self.headers}

    def _paginate_iter(
        self,
        path: str,
        params: dict,
        deserializer,
        result_key: str = "results",
        options: Optional[RequestOptionBuilder] = None,
    ):
        max_items_to_return = None
        if "max_num_to_return" in params:
            max_items_to_return = params["max_num_to_return"]
        returned_items = 0
        while True:
            resp = self._get(
                path=path,
                params=params,
                deserializer=deserializer,
                result_key=result_key,
                raw=True,
                options=options,
            )

            try:
                decoded = self._decode(resp)
            except ValueError as e:
                print(f"Error decoding json response: {e}")
                return []

            if result_key not in decoded:
                return []
            for t in decoded[result_key]:
                if max_items_to_return and returned_items == max_items_to_return:
                    return
                returned_items += 1
                yield deserializer(t)

            if "next_url" in decoded:
                next_url = decoded["next_url"]
                parsed = urlparse(next_url)
                path = parsed.path
                if parsed.query:
                    path += "?" + parsed.query
                params = {}
            else:
                return

    def _paginate(
        self,
        path: str,
        params: dict,
        raw: bool,
        deserializer,
        result_key: str = "results",
        options: Optional[RequestOptionBuilder] = None,
    ):
        if raw:
            return self._get(
                path=path,
                params=params,
                deserializer=deserializer,
                raw=True,
                options=options,
            )

        return self._paginate_iter(
            path=path,
            params=params,
            deserializer=deserializer,
            result_key=result_key,
            options=options,
        )
