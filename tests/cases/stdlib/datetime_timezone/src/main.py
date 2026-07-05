# datetime v3 fixed-offset timezone + aware datetime core: construction and
# validation, utcoffset/tzname/dst, eq/hash by offset, repr/str, aware
# arithmetic and comparisons (UTC-normalized), runtime naive/aware mixing
# rules (== False, ordering/subtraction TypeError), replace() incl. the
# tzinfo set/drop forms, isoformat offset suffix + timespec, combine and
# fromtimestamp with a tz.
from datetime import datetime, date, time, timedelta, timezone, UTC


def main() -> None:
    ist = timezone(timedelta(hours=5, minutes=30), "IST")
    unnamed = timezone(timedelta(hours=-3, minutes=-30))
    withsec = timezone(timedelta(hours=5, minutes=30, seconds=15))

    print(repr(UTC), str(UTC))
    print(repr(ist), str(ist))
    print(repr(unnamed), str(unnamed))
    print(repr(withsec), str(withsec))
    print(repr(timezone(timedelta(), "EMPTYOK")))
    # Empty string is a real name, distinct from the unnamed form.
    empty_name = timezone(timedelta(hours=1), "")
    no_name = timezone(timedelta(hours=1))
    print(repr(empty_name), repr(empty_name.tzname(None)))
    print(repr(no_name), no_name.tzname(None))
    print(empty_name == no_name)  # offset-only eq: True
    print(ist.utcoffset(None), ist.tzname(None), ist.dst(None) is None)
    print(timezone(timedelta(hours=5, minutes=30), "OTHER") == ist)  # offset-only eq
    print(hash(timezone(timedelta(hours=5, minutes=30))) == hash(ist))
    print(timezone(timedelta()) == UTC)
    try:
        timezone(timedelta(hours=24))
    except ValueError:
        print("ValueError-offset-hi")
    try:
        timezone(timedelta(hours=-24))
    except ValueError:
        print("ValueError-offset-lo")
    print(repr(timezone(timedelta(hours=23, minutes=59, microseconds=999999))))

    dt = datetime(2021, 3, 5, 14, 30, 15, 123456, tzinfo=ist)
    u = datetime(2021, 3, 5, 9, 0, 15, 123456, tzinfo=UTC)
    print(repr(dt))
    print(dt.isoformat(), str(dt))
    print(dt.utcoffset(), dt.tzname(), dt.dst() is None)
    print(dt == u, dt <= u, dt >= u, hash(dt) == hash(u))
    print(u - dt)
    print(dt - datetime(2021, 3, 4, 9, 0, 15, 123456, tzinfo=UTC))
    print(dt + timedelta(hours=2))
    print(dt - timedelta(minutes=45))

    naive = datetime(2021, 3, 5, 9, 0, 15, 123456)
    print(naive == u, naive != u)
    try:
        print(naive < u)
    except TypeError:
        print("TypeError-order")
    try:
        print(u - naive)
    except TypeError:
        print("TypeError-sub")

    print(dt.replace(hour=8))
    try:
        dt.replace(month=13)
    except ValueError:
        print("ValueError-replace-month")
    try:
        time(14, 30).replace(hour=25)
    except ValueError:
        print("ValueError-replace-hour")
    try:
        date(2021, 2, 28).replace(day=30)
    except ValueError:
        print("ValueError-replace-day")
    print(dt.replace(tzinfo=None))
    print(dt.replace(tzinfo=UTC))
    print(dt.replace(year=1999, minute=0, tzinfo=None))
    print(naive.replace(tzinfo=ist))
    try:
        print(dt.replace(tzinfo=False))
    except TypeError:
        print("TypeError-replace-tzinfo")

    t = datetime(2021, 3, 5, 14, 30, 15, 999999, tzinfo=withsec)
    print(t.isoformat())
    print(t.isoformat(timespec="hours"))
    print(t.isoformat(timespec="minutes"))
    print(t.isoformat(timespec="seconds"))
    print(t.isoformat(timespec="milliseconds"))  # truncates, never rounds
    print(t.isoformat(timespec="microseconds"))
    try:
        print(t.isoformat(timespec="bogus"))
    except ValueError:
        print("ValueError-timespec")

    print(datetime.combine(date(2021, 3, 5), time(9, 15), UTC))
    print(datetime.fromtimestamp(1614937200.5, UTC))
    print(datetime.fromtimestamp(1614937200.5, ist))
    print(repr(ist.fromutc(datetime(2021, 3, 5, 9, 0, tzinfo=ist))))
    try:
        ist.fromutc(datetime(2021, 3, 5, 9, 0, tzinfo=UTC))
    except ValueError:
        print("ValueError-fromutc")

    # Aware/naive values as dict keys: aware pairs at the same instant
    # collapse to one key.
    d = {dt: "a", u: "b", naive: "c"}
    print(len(d), d[dt])


main()
