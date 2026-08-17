"""Configuration loading for the ingestion framework.

Settings are represented as frozen dataclasses and loaded from a YAML file
(falling back to built-in defaults). This keeps configuration explicit and
testable without pulling in a heavier config framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - dependency guard
    raise SystemExit(
        "PyYAML is required. Install dependencies with: pip install -r requirements.txt"
    ) from exc


VALID_TABS = ("open", "past", "cancelled", "upcoming", "corrigendum")


@dataclass(frozen=True)
class PortalSettings:
    base_url: str = "https://eproc2.bihar.gov.in/EPSV2Web"
    # Root organizations whose department hierarchy is loaded into dim_department
    # (for name enrichment). 538 = "Government of Bihar", the top-level root whose
    # tree contains every department, so the default covers the whole portal.
    root_org_ids: tuple[int, ...] = (538,)
    # Organization the listing crawl is scoped to. 538 is the portal root, so it
    # returns every tender. Set to None (YAML null) to send an empty filter,
    # which is equivalent (whole portal); set to a sub-org id to pilot a subset.
    discovery_org_id: int | None = 538


@dataclass(frozen=True)
class HttpSettings:
    request_delay_seconds: float = 0.75
    timeout_seconds: float = 60.0
    max_retries: int = 4
    backoff_base_seconds: float = 1.5
    backoff_max_seconds: float = 30.0
    user_agent: str = "BEDIF/0.1 (+public-procurement-analytics)"
    verify_tls: bool = True
    ca_bundle: str | None = None
    # The portal's bearer token expires server-side (~11 min observed); a stale
    # token yields HTTP 500. Proactively refresh it this often (seconds).
    token_refresh_seconds: float = 420.0


@dataclass(frozen=True)
class PaginationSettings:
    page_size: int = 100
    max_pages: int = 0


@dataclass(frozen=True)
class CrawlSettings:
    tabs: tuple[str, ...] = VALID_TABS
    force_refetch: bool = False


@dataclass(frozen=True)
class Settings:
    portal: PortalSettings = field(default_factory=PortalSettings)
    http: HttpSettings = field(default_factory=HttpSettings)
    pagination: PaginationSettings = field(default_factory=PaginationSettings)
    crawl: CrawlSettings = field(default_factory=CrawlSettings)
    database_path: Path = Path("data/bihar_eproc.db")
    raw_json_dir: Path | None = Path("storage/raw_json")

    @property
    def context_path(self) -> str:
        return self.portal.base_url.rstrip("/")


def _as_int_tuple(values: Any, default: tuple[int, ...]) -> tuple[int, ...]:
    if values is None:
        return default
    return tuple(int(v) for v in values)


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load settings from a YAML file, merging over built-in defaults."""

    data: dict[str, Any] = {}
    if config_path is not None:
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    portal_raw = data.get("portal", {}) or {}
    http_raw = data.get("http", {}) or {}
    page_raw = data.get("pagination", {}) or {}
    crawl_raw = data.get("crawl", {}) or {}
    db_raw = data.get("database", {}) or {}
    storage_raw = data.get("storage", {}) or {}

    portal = PortalSettings(
        base_url=portal_raw.get("base_url", PortalSettings.base_url),
        root_org_ids=_as_int_tuple(
            portal_raw.get("root_org_ids"), PortalSettings.root_org_ids
        ),
        discovery_org_id=_as_optional_int(
            portal_raw.get("discovery_org_id", PortalSettings.discovery_org_id)
        ),
    )
    http = HttpSettings(
        request_delay_seconds=float(
            http_raw.get("request_delay_seconds", HttpSettings.request_delay_seconds)
        ),
        timeout_seconds=float(http_raw.get("timeout_seconds", HttpSettings.timeout_seconds)),
        max_retries=int(http_raw.get("max_retries", HttpSettings.max_retries)),
        backoff_base_seconds=float(
            http_raw.get("backoff_base_seconds", HttpSettings.backoff_base_seconds)
        ),
        backoff_max_seconds=float(
            http_raw.get("backoff_max_seconds", HttpSettings.backoff_max_seconds)
        ),
        user_agent=http_raw.get("user_agent", HttpSettings.user_agent),
        verify_tls=bool(http_raw.get("verify_tls", HttpSettings.verify_tls)),
        ca_bundle=http_raw.get("ca_bundle", HttpSettings.ca_bundle),
        token_refresh_seconds=float(
            http_raw.get("token_refresh_seconds", HttpSettings.token_refresh_seconds)
        ),
    )
    pagination = PaginationSettings(
        page_size=int(page_raw.get("page_size", PaginationSettings.page_size)),
        max_pages=int(page_raw.get("max_pages", PaginationSettings.max_pages)),
    )
    tabs = crawl_raw.get("tabs")
    if tabs:
        tabs = tuple(str(t).lower() for t in tabs)
        invalid = [t for t in tabs if t not in VALID_TABS]
        if invalid:
            raise ValueError(f"Unknown crawl tabs {invalid}; valid: {VALID_TABS}")
    else:
        tabs = CrawlSettings.tabs
    crawl = CrawlSettings(
        tabs=tabs,
        force_refetch=bool(crawl_raw.get("force_refetch", CrawlSettings.force_refetch)),
    )

    raw_dir = storage_raw.get("raw_json_dir", "storage/raw_json")
    raw_json_dir = Path(raw_dir) if raw_dir else None

    return Settings(
        portal=portal,
        http=http,
        pagination=pagination,
        crawl=crawl,
        database_path=Path(db_raw.get("path", "data/bihar_eproc.db")),
        raw_json_dir=raw_json_dir,
    )


def with_overrides(settings: Settings, **overrides: Any) -> Settings:
    """Return a copy of settings with top-level fields replaced."""

    return replace(settings, **overrides)
