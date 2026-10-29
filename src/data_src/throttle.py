"""Cross-process request throttle and cooldown for external data sources.

Every provider call runs in its own spawned process, so spacing has to be shared: each source
family keeps its next free slot in a small SQLite file, and a caller reserves a slot before it
sends a request. A source that answers with a ban or rate-limit signal is put in cooldown; calls
during the cooldown fail at once without touching the server.

Design notes: docs/modules/1-数据/数据获取底层接口模块.md「请求节流与冷却」.
"""

from __future__ import annotations

import os
import random
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from .common import DataSourceError

# Minimum seconds between two requests to one source family, shared by all processes.
INTERVALS: dict[str, float] = {
    "baostock:login": 3.0,
    "baostock": 0.3,
    "eastmoney": 0.5,
    "sina": 0.5,
    "ths": 1.0,
    "tencent": 0.3,
    "csindex": 0.5,
    "cninfo": 0.5,
    "sse": 0.5,
    "szse": 0.5,
    "bse": 0.5,
    "chinamoney": 0.5,
    "stats": 1.0,
    "mootdx": 0.2,
}
DEFAULT_INTERVAL = 0.2
JITTER = 0.3
HTTP_COOLDOWN_SECONDS = 120.0
_HOST_FAMILIES = (
    ("eastmoney.com", "eastmoney"),
    ("sina.com.cn", "sina"),
    ("sinajs.cn", "sina"),
    ("10jqka.com.cn", "ths"),
    ("qq.com", "tencent"),
    ("gtimg.cn", "tencent"),
    ("csindex.com.cn", "csindex"),
    ("cninfo.com.cn", "cninfo"),
    ("sse.com.cn", "sse"),
    ("szse.cn", "szse"),
    ("bse.cn", "bse"),
    ("chinamoney.com.cn", "chinamoney"),
    ("stats.gov.cn", "stats"),
)
_DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "database" / "source_throttle.sqlite3"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "unknown"})


class SourceCoolingDown(DataSourceError):
    """The source signalled a ban or rate limit recently; no request is sent until the cooldown ends."""


class SourceThrottle:
    def __init__(self, db_path: str | Path, *, clock=time.time, sleep=time.sleep) -> None:
        self.db_path = Path(db_path)
        self.clock = clock
        self.sleep = sleep
        self._local_next: dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str) -> None:
        """Wait for this source's next free slot; raise SourceCoolingDown during a cooldown."""

        interval = INTERVALS.get(key, DEFAULT_INTERVAL) * (1 + random.random() * JITTER)
        try:
            wait = self._reserve(key, interval)
        except sqlite3.Error:
            wait = self._reserve_locally(key, interval)
        if wait > 0:
            self.sleep(wait)

    def trip(self, key: str, seconds: float, reason: str) -> None:
        until = self.clock() + seconds
        try:
            with closing(self._connect()) as conn:
                conn.execute(
                    "INSERT INTO source_slots (key, next_at, blocked_until, reason) VALUES (?, 0, ?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET blocked_until = excluded.blocked_until, reason = excluded.reason",
                    (key, until, reason[:300]),
                )
        except sqlite3.Error:
            pass

    def cooldowns(self) -> dict[str, dict[str, object]]:
        now = self.clock()
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT key, blocked_until, reason FROM source_slots WHERE blocked_until > ?", (now,)
                ).fetchall()
        except sqlite3.Error:
            return {}
        return {
            key: {"until": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(until)), "reason": reason}
            for key, until, reason in rows
        }

    def _reserve(self, key: str, interval: float) -> float:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT next_at, blocked_until, reason FROM source_slots WHERE key = ?", (key,)
            ).fetchone()
            now = self.clock()
            if row is not None and row[1] > now:
                conn.execute("COMMIT")
                until = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row[1]))
                raise SourceCoolingDown(f"{key} 冷却中，至 {until} 前不发请求：{row[2]}")
            slot = max(now, row[0] if row is not None else 0.0)
            conn.execute(
                "INSERT INTO source_slots (key, next_at, blocked_until, reason) VALUES (?, ?, 0, '') "
                "ON CONFLICT (key) DO UPDATE SET next_at = excluded.next_at",
                (key, slot + interval),
            )
            conn.execute("COMMIT")
        return slot - now

    def _reserve_locally(self, key: str, interval: float) -> float:
        # The shared file is unusable (locked too long, disk error): still space this process's calls.
        with self._lock:
            now = self.clock()
            slot = max(now, self._local_next.get(key, 0.0))
            self._local_next[key] = slot + interval
        return slot - now

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS source_slots ("
            "key TEXT PRIMARY KEY, next_at REAL NOT NULL, blocked_until REAL NOT NULL, reason TEXT NOT NULL)"
        )
        return conn


class _Disabled:
    def acquire(self, key: str) -> None:
        return None

    def trip(self, key: str, seconds: float, reason: str) -> None:
        return None

    def cooldowns(self) -> dict[str, dict[str, object]]:
        return {}


_DEFAULT: SourceThrottle | _Disabled | None = None
NO_THROTTLE = _Disabled()


def default_throttle() -> SourceThrottle | _Disabled:
    """Shared throttle; WEALTH_FARM_THROTTLE=off disables it, WEALTH_FARM_THROTTLE_DB moves its file."""

    global _DEFAULT
    if _DEFAULT is None:
        if os.environ.get("WEALTH_FARM_THROTTLE", "").lower() == "off":
            _DEFAULT = _Disabled()
        else:
            _DEFAULT = SourceThrottle(os.environ.get("WEALTH_FARM_THROTTLE_DB") or _DEFAULT_DB)
    return _DEFAULT


def host_family(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for suffix, family in _HOST_FAMILIES:
        if host == suffix or host.endswith("." + suffix):
            return family
    return host or "unknown"


def install_http_throttle() -> None:
    """Route every requests and urllib call (ours and libraries' such as AkShare) through the throttle; idempotent."""

    _install_urllib()
    try:
        import requests
    except ImportError:
        return
    original = requests.sessions.Session.request
    if getattr(original, "_wealth_farm_throttled", False):
        return

    def request(self, method, url, *args, **kwargs):
        family = host_family(str(url))
        if family in _LOCAL_HOSTS:
            return original(self, method, url, *args, **kwargs)
        throttle = default_throttle()
        throttle.acquire(family)
        response = original(self, method, url, *args, **kwargs)
        if response.status_code in (403, 429):
            throttle.trip(family, HTTP_COOLDOWN_SECONDS, f"HTTP {response.status_code} from {urlsplit(str(url)).hostname}")
        return response

    request._wealth_farm_throttled = True
    requests.sessions.Session.request = request


def _install_urllib() -> None:
    import urllib.error
    import urllib.request

    original = urllib.request.OpenerDirector.open
    if getattr(original, "_wealth_farm_throttled", False):
        return

    def open_(self, fullurl, *args, **kwargs):
        url = fullurl if isinstance(fullurl, str) else fullurl.full_url
        family = host_family(url)
        if family in _LOCAL_HOSTS:
            return original(self, fullurl, *args, **kwargs)
        throttle = default_throttle()
        throttle.acquire(family)
        try:
            return original(self, fullurl, *args, **kwargs)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                throttle.trip(family, HTTP_COOLDOWN_SECONDS, f"HTTP {exc.code} from {urlsplit(url).hostname}")
            raise

    open_._wealth_farm_throttled = True
    urllib.request.OpenerDirector.open = open_
