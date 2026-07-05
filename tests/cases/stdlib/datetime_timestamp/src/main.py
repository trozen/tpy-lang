# datetime v3 timestamp()/astimezone() under a pinned IANA zone with DST.
# TZ is set to Europe/Warsaw BEFORE any datetime use: TPy's tz backend
# resolves TZ once at first use, and CPython's libc rereads it per call,
# so setting it first thing makes both sides deterministic (offsets and
# CET/CEST abbreviations come from the same OS tz database). Exercises the
# iterative local-inverse solve: normal winter/summer instants, the
# 2021-03-28 02:30 spring-forward gap (fold=0 -> later instant) and the
# 2021-10-31 02:30 fall-back fold (fold=0 -> earlier instant) -- a single
# forward offset lookup would silently produce wrong timestamps here.
import os
import time as _time
os.environ["TZ"] = "Europe/Warsaw"
_time.tzset()  # CPython needs it; TPy pins TZ at first use (no-op)

from datetime import datetime, timedelta, timezone, UTC


def main() -> None:
    ist = timezone(timedelta(hours=5, minutes=30), "IST")

    # Aware timestamps: pure UTC math, zone-independent.
    print(datetime(2021, 3, 5, 14, 30, 15, 500000, tzinfo=ist).timestamp())
    print(datetime(1970, 1, 1, tzinfo=UTC).timestamp())
    print(datetime(1969, 12, 31, 23, 59, 59, 250000, tzinfo=UTC).timestamp())

    # Naive timestamps = local wall clock in the pinned zone.
    print(datetime(2021, 1, 15, 12, 0).timestamp())
    print(datetime(2021, 7, 15, 12, 0).timestamp())
    print(datetime(2021, 3, 28, 2, 30).timestamp())    # DST gap
    print(datetime(2021, 10, 31, 2, 30).timestamp())   # DST fold
    print(datetime(2021, 1, 15, 12, 0, 0, 123456).timestamp())

    # fromtimestamp/timestamp round-trip through local time.
    print(datetime.fromtimestamp(datetime(2021, 7, 15, 12, 0).timestamp()))

    # astimezone: aware->aware, aware->local, naive->fixed, naive->local
    # (naive input converts, it does NOT raise -- unlike comparison).
    aware = datetime(2021, 3, 5, 14, 30, 15, 500000, tzinfo=ist)
    print(aware.astimezone(UTC))
    print(aware.astimezone(timezone(timedelta(hours=-8))))
    print(aware.astimezone())
    print(repr(aware.astimezone()))
    n = datetime(2021, 7, 15, 12, 0)
    print(n.astimezone(UTC))
    print(repr(n.astimezone()))
    w = datetime(2021, 1, 15, 12, 0)
    print(repr(w.astimezone()))
    print(w.astimezone().timestamp() == w.timestamp())
    print(w.astimezone(UTC).astimezone().replace(tzinfo=None) == w)


main()
