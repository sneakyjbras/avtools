from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Type, TypeVar
from urllib.parse import urljoin

import structlog
from requests import Response, Session
from requests.auth import HTTPBasicAuth
from requests.exceptions import RequestException

from avtools.eam.config import EAMConfig
from avtools.eam.device import EAMDevice
from avtools.eam.position import EAMPosition
from avtools.exception.errors import NoRecordsFound

T = TypeVar("T")  # generic model (EAMDevice, EAMPosition, ...)

client_logger = structlog.get_logger(__name__).bind(
    component="eam",
    model="EAMClient",
)


class EAMClient:
    """
    Client for fetching record counts and device/position lists from the EAM API.

    Note: requests.Session is not thread-safe; prefer one client per thread.
    """

    # --- Lifecycle ----------------------------------------------------------

    def __init__(
        self,
        authorization: HTTPBasicAuth,
        config: EAMConfig | None = None,
        session: Session | None = None,
    ) -> None:
        self.config = config or EAMConfig()
        self._owns_session = session is None
        self._session = session or Session()
        self._session.auth = authorization
        self._session.headers.update(self.config.headers)

    def __enter__(self) -> EAMClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._owns_session:
            self._session.close()

    # --- HTTP / JSON helpers ------------------------------------------------

    def _endpoint_url(self) -> str:
        """Build the full EAM grid data endpoint URL."""
        return urljoin(
            self.config.base_url.rstrip("/") + "/", self.config.endpoint.lstrip("/")
        )

    def _request(self, payload: Mapping[str, Any]) -> Response:
        """POST to the EAM grids/data endpoint. Raises on HTTP error."""
        url = self._endpoint_url()
        try:
            resp = self._session.post(
                url,
                json=dict(payload),
                timeout=self.config.timeout,
                verify=self.config.verify,
            )
            resp.raise_for_status()
            return resp
        except RequestException as e:
            body_preview = ""
            try:
                body_preview = (resp.text if "resp" in locals() else "")[:500]  # type: ignore[name-defined]
            except Exception:
                pass

            client_logger.error(
                "eam_api_request_failed",
                url=url,
                payload_keys=list(payload.keys()),
                rowCount=payload.get("rowCount"),
                gridID=payload.get("gridID"),
                gridName=payload.get("gridName"),
                error=str(e),
                body_preview=body_preview,
                exc_info=True,
            )
            raise

    @staticmethod
    def _json(resp: Response) -> Any:
        """Parse JSON from a response with logging on failure."""
        try:
            return resp.json()
        except ValueError:
            client_logger.error(
                "eam_invalid_json_response",
                status_code=resp.status_code,
                text_preview=resp.text[:500],
            )
            return None

    @staticmethod
    def _extract(obj: Any, *keys: Any, default: Any = None) -> Any:
        """Safely walk nested dicts/lists by keys or integer indices."""
        cur = obj
        for k in keys:
            if isinstance(cur, dict) and isinstance(k, str):
                cur = cur.get(k)
            elif isinstance(cur, list) and isinstance(k, int) and 0 <= k < len(cur):
                cur = cur[k]
            else:
                return default
            if cur is None:
                return default
        return cur if cur is not None else default

    # --- Counts -------------------------------------------------------------

    def _get_total_records(self, grid_id: int | str, grid_name: str) -> int:
        """Return total records for a given grid."""
        # Prefer config builders to ensure deep copy & correct filter setup
        payload = self.config.make_query()
        payload.update(
            {
                "gridID": grid_id,
                "userFunctionName": grid_name,
                "gridName": grid_name,
                # rowCount=0 means "count only" in your defaults
                "rowCount": 0,
            }
        )
        resp = self._request(payload)
        data = self._json(resp)
        records = self._extract(data, "data", "records", default=0)
        try:
            return int(records)
        except (TypeError, ValueError):
            client_logger.warning(
                "eam_non_integer_records_field",
                grid_id=grid_id,
                grid_name=grid_name,
                records=records,
            )
            return 0

    def get_number_av_assets(self) -> int:
        """Get total number of AV assets in EAM."""
        return self._get_total_records(
            self.config.asset_grid_id,
            self.config.asset_grid_name,
        )

    def get_number_av_positions(self) -> int:
        """Get total number of AV positions in EAM."""
        return self._get_total_records(
            self.config.position_grid_id,
            self.config.position_grid_name,
        )

    # --- Model coercion -----------------------------------------------------

    def _coerce_model(
        self,
        parser: type[T] | Callable[[Mapping[str, Any]], T],
        row: Mapping[str, Any],
    ) -> T:
        """
        Convert a raw row dict into a model instance.

        Supports:
          - Pydantic v2: parser.model_validate(row)
          - Pydantic v1: parser.parse_obj(row)
          - Callable parser(row)
          - Dataclass-like: parser(**row)
        """
        if callable(parser) and not isinstance(parser, type):
            return parser(row)

        if hasattr(parser, "model_validate"):
            return parser.model_validate(row)  # type: ignore[attr-defined]

        if hasattr(parser, "parse_obj"):
            return parser.parse_obj(row)  # type: ignore[attr-defined]

        try:
            return parser(**row)  # type: ignore[misc]
        except TypeError:
            return parser(row)  # type: ignore[call-arg]

    # --- List fetch ---------------------------------------------------------

    def _fetch_list(
        self,
        grid_id: int | str,
        grid_name: str,
        missing_msg: str,
        row_count: int,
        parser: type[T] | Callable[[Mapping[str, Any]], T],
        *,
        log_each: bool = True,
    ) -> list[T]:
        """Fetch rows from EAM and return them as parser model instances."""
        # Prefer config builders to ensure deep copy & correct filter setup
        payload = self.config.make_query()
        payload.update(
            {
                "rowCount": row_count,
                "gridID": grid_id,
                "userFunctionName": grid_name,
                "gridName": grid_name,
            }
        )
        resp = self._request(payload)
        data = self._json(resp)
        rows = self._extract(data, "data", "row", default=[]) or []

        if not isinstance(rows, list) or not rows:
            raise NoRecordsFound(missing_msg)

        items: list[T] = []
        for row in rows:
            if not isinstance(row, Mapping):
                client_logger.warning(
                    "eam_skipping_non_dict_row",
                    grid_name=grid_name,
                    row_preview=str(row)[:200],
                )
                continue

            obj = self._coerce_model(parser, row)

            if log_each:
                log_fn = getattr(obj, "log_device", None)
                if callable(log_fn):
                    try:
                        log_fn()
                    except Exception:
                        client_logger.warning(
                            "eam_log_device_failed",
                            grid_name=grid_name,
                            exc_info=True,
                        )

            items.append(obj)

        client_logger.info(
            "eam_finished_processing_rows",
            grid_id=grid_id,
            grid_name=grid_name,
            count=len(items),
        )
        return items

    # --- Public API ---------------------------------------------------------

    def get_device_list(self, records: int) -> list[EAMDevice]:
        """Fetch AV device data as a list of EAMDevice instances."""
        return self._fetch_list(
            self.config.asset_grid_id,
            self.config.asset_grid_name,
            "No records found for any AV class type in EAM.",
            records,
            parser=EAMDevice,
        )

    def get_positions_list(self, records: int) -> list[EAMPosition]:
        """Fetch AV position data as a list of EAMPosition instances."""
        return self._fetch_list(
            self.config.position_grid_id,
            self.config.position_grid_name,
            "No records found for any AV position in EAM.",
            records,
            parser=EAMPosition,
        )
