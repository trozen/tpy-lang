# datetime.date() / datetime.time() accessors: split a datetime into its date
# and (naive) time components. time() drops tzinfo like CPython.
# Byte-compared against real CPython datetime.
from datetime import datetime, timezone, timedelta


def main() -> None:
    dt = datetime(2026, 7, 6, 14, 30, 45, 123456)
    d = dt.date()
    t = dt.time()
    print(d.year, d.month, d.day)
    print(t.hour, t.minute, t.second, t.microsecond)
    print(d.isoformat(), t.isoformat())
    # round-trip: combine back to the original datetime
    print(datetime.combine(d, t) == dt)

    # aware datetime: time() drops the tzinfo (naive result)
    aware = datetime(2026, 1, 2, 8, 15, 0, 0,
                     tzinfo=timezone(timedelta(hours=2)))
    at = aware.time()
    print(at.hour, at.minute, at.second)
    print(aware.date().isoformat())

    # midnight / zero components
    z = datetime(1, 1, 1)
    print(z.date().isoformat(), z.time().isoformat())


main()
