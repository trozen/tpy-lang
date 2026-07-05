# datetime v4 conversions: fromtimestamp/now/astimezone/combine with a
# ZoneInfo tz. fromtimestamp must set fold=1 on the SECOND pass of a
# repeated wall time (both instants of Warsaw's 2023-10-29 fold window are
# checked, plus the naive-local detection under pinned TZ); astimezone
# converts naive/fixed/zone sources through the real instant.
import os
import time as _time
os.environ["TZ"] = "Europe/Warsaw"
_time.tzset()  # CPython needs it; TPy pins TZ at first use (no-op)

from datetime import date, datetime, time, timedelta, timezone, UTC
from zoneinfo import ZoneInfo


def main() -> None:
    waw = ZoneInfo("Europe/Warsaw")
    # 2023-10-29 02:30 Warsaw occurs at 1698539400 (CEST, first pass) and
    # 1698543000 (CET, second pass).
    first = datetime.fromtimestamp(1698539400.0, waw)
    second = datetime.fromtimestamp(1698543000.0, waw)
    print(first, first.fold)
    print(second, second.fold)
    print(repr(second))
    print(first.timestamp(), second.timestamp())

    # Naive-local fromtimestamp detects the fold the same way.
    nfirst = datetime.fromtimestamp(1698539400.0)
    nsecond = datetime.fromtimestamp(1698543000.0)
    print(nfirst, nfirst.fold, nsecond, nsecond.fold)
    print(nfirst.timestamp(), nsecond.timestamp())

    # A fixed-offset target never folds.
    utc_dt = datetime.fromtimestamp(1698543000.0, UTC)
    print(utc_dt, utc_dt.fold)

    # astimezone: naive (system-local) and fixed sources into a zone, and
    # zone -> zone across the Atlantic.
    ny = ZoneInfo("America/New_York")
    print(datetime(2023, 7, 15, 12, 0).astimezone(ny))
    fixed = datetime(2023, 7, 15, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    print(fixed.astimezone(ny))
    aware = datetime(2023, 7, 15, 12, 0, tzinfo=waw)
    print(aware.astimezone(ny))
    print(aware.astimezone(ny).astimezone(waw) == aware)
    # Into the fold window: astimezone sets fold on the second pass.
    back = datetime.fromtimestamp(1698543000.0, UTC).astimezone(waw)
    print(back, back.fold)

    # combine + now with a zone tz (now is invariant-checked only).
    print(datetime.combine(date(2023, 7, 15), time(12, 0), waw))
    n = datetime.now(waw)
    ntz = n.tzinfo
    print(ntz is not None and isinstance(ntz, ZoneInfo) and ntz == waw,
          n.utcoffset() is not None)


main()
