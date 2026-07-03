# datetime v2 wall-clock surface: now/utcnow/today/fromtimestamp and
# date.today. Values are host- and time-dependent, so every line prints an
# invariant (relationship), never an absolute local timestamp -- output.txt
# must be identical on every host. Local-offset sanity bounds come from the
# tz database's physical range (UTC-12..UTC+14).
from datetime import date, datetime, timedelta


def main() -> None:
    lo = datetime(2026, 1, 1)
    hi = datetime(2100, 1, 1)

    now = datetime.now()
    utc = datetime.utcnow()
    print(lo < now, now < hi)               # True True
    print(lo < utc, utc < hi)               # True True
    print(0 <= now.hour, now.hour <= 23)    # True True
    print(0 <= now.microsecond, now.microsecond <= 999999)  # True True

    # now() and utcnow() differ by the local UTC offset plus the instant
    # gap between the two calls; bound it by the tz db's physical range.
    off = now - utc
    print(-timedelta(hours=13) < off, off < timedelta(hours=15))  # True True

    # today() is now() at date precision; two consecutive calls bracket it.
    d1 = date.today()
    dt = datetime.today()
    d2 = date.today()
    print(d1 <= d2, d1.year >= 2026)        # True True
    print(d1 <= date(dt.year, dt.month, dt.day))  # True (no midnight race)
    print(date(dt.year, dt.month, dt.day) <= d2)  # True

    # Wall-clock never goes backwards across consecutive now() calls by more
    # than a leap adjustment; assert monotone-ish ordering loosely.
    n2 = datetime.now()
    print(n2 - now < timedelta(minutes=5))  # True (same process, no sleep)

    # fromtimestamp is the local-time inverse of the epoch: the same second
    # printed twice must agree with the utc variant modulo the local offset,
    # which is a whole number of minutes for every real zone.
    local = datetime.fromtimestamp(1751551805.5)
    utcv = datetime.utcfromtimestamp(1751551805.5)
    delta = local - utcv
    print(local.microsecond == utcv.microsecond)  # True (offset is whole seconds)
    print(-timedelta(hours=13) < delta, delta < timedelta(hours=15))  # True True
    mins = delta // timedelta(minutes=1)
    print(delta == timedelta(minutes=1) * mins)   # True (whole minutes)


main()
