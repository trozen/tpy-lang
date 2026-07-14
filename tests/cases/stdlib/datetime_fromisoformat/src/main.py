# datetime v3 fromisoformat (3.11+ grammar) on date/time/datetime: basic
# YYYYMMDD, ISO week dates, any single separator char, truncated time
# forms, comma fractions, 7+ fraction digits truncating, case-sensitive Z
# vs case-insensitive separator, offsets with seconds/fractions, and the
# reject matrix. time.fromisoformat rejects an offset suffix (TPy's time
# is naive-only; aware time deferred -- documented divergence, loud).
from datetime import datetime, date, time, timedelta, timezone


def sd(s: str) -> None:
    try:
        print(repr(date.fromisoformat(s)))
    except ValueError:
        print("ValueError")


def st(s: str) -> None:
    try:
        print(repr(time.fromisoformat(s)))
    except ValueError:
        print("ValueError")


def sdt(s: str) -> None:
    try:
        print(repr(datetime.fromisoformat(s)))
    except ValueError:
        print("ValueError")


def main() -> None:
    sd("2021-03-05")
    sd("20210305")
    sd("2021-W01-1")
    sd("2021W011")
    sd("2020-W53-1")
    sd("2021-W53-1")
    sd("2021-W011")
    sd("2021-02-30")
    sd("2021-3-5")
    sd("+021-03-05")
    sd("2021 03 05")

    st("14:30")
    st("14:30:15")
    st("14:30:15.123")
    st("14:30:15.123456789")
    st("T14:30")
    st("1430")
    st("143015")
    st("14")
    st("14:30:15,5")
    # 25:00 (out-of-range hour) rejects on all versions; "24:00" is avoided
    # because CPython 3.14 accepts it as midnight while TPy/older reject it.
    st("25:00")
    st("14:60")

    sdt("2021-03-05T14:30")
    sdt("2021-03-05 14:30:15.123456")
    sdt("2021-03-05X14:30")
    sdt("2021-03-05t14:30")
    sdt("20210305T1430")
    sdt("20210305T143015")
    sdt("2021-03-05T14:30:15.123456789+05:00")
    sdt("2021-03-05T14:30Z")
    sdt("2021-03-05T14:30z")
    sdt("2021-03-05T14:30+00:00")
    sdt("2021-03-05T14:30-05:30")
    sdt("2021-03-05T14:30+0530")
    sdt("2021-03-05T14:30+05")
    sdt("2021-03-05T14:30+05:30:15")
    sdt("2021-03-05T14:30+05:30:15.250000")
    sdt("2021-03-05T14:30+5:30")
    sdt("2021-03-05")
    sdt("2021-W01-1T14:30")
    sdt("2021-02-30T14:30")
    sdt("2021-03-05T14:30:15.")
    sdt("2021-03-05T25:30")
    sdt("2021-03-05T14:30+99:00")

    # Round-trips: isoformat output parses back to the same value.
    ist = timezone(timedelta(hours=5, minutes=30), "IST")
    a = datetime(2021, 3, 5, 14, 30, 15, 123456, tzinfo=ist)
    print(datetime.fromisoformat(a.isoformat()) == a)
    b = datetime(2021, 3, 5, 14, 30)
    print(datetime.fromisoformat(b.isoformat()) == b)
    c = date(2021, 12, 31)
    print(date.fromisoformat(c.isoformat()) == c)
    t = time(23, 59, 59, 999999)
    print(time.fromisoformat(t.isoformat()) == t)


main()
