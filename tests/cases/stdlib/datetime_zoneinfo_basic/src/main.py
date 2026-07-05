# datetime v4 ZoneInfo basics: key/repr/str, per-instant utcoffset/dst/
# tzname (winter CET vs summer CEST), aware repr/isoformat/strftime, the
# datetime-less tzinfo calls, equality by key, and the .tzinfo round-trip.
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def main() -> None:
    waw = ZoneInfo("Europe/Warsaw")
    print(repr(waw))
    print(str(waw), waw.key)
    print(waw == ZoneInfo("Europe/Warsaw"))
    print(waw.utcoffset(None), waw.tzname(None), waw.dst(None))

    winter = datetime(2023, 1, 15, 12, 0, tzinfo=waw)
    summer = datetime(2023, 7, 15, 12, 0, tzinfo=waw)
    print(winter.utcoffset(), winter.tzname(), winter.dst())
    print(summer.utcoffset(), summer.tzname(), summer.dst())
    print(waw.utcoffset(summer), waw.tzname(summer), waw.dst(summer))
    print(repr(summer))
    print(summer.isoformat(), summer.isoformat(" ", "minutes"))
    print(summer.strftime("%Y-%m-%d %H:%M %z %Z"))

    # A zone with no DST ever: offset 0 forever, dst 0 (not None).
    rey = ZoneInfo("Atlantic/Reykjavik")
    at = datetime(2023, 7, 1, 8, 0, tzinfo=rey)
    print(at.utcoffset(), at.tzname(), at.dst())

    # The .tzinfo property reconstructs an equal ZoneInfo value.
    tz = summer.tzinfo
    print(tz is not None and isinstance(tz, ZoneInfo) and tz == waw)
    if tz is not None:
        print(repr(tz))

    # Fixed-offset timezone stays distinct from ZoneInfo in the union.
    mixed = datetime(2023, 7, 15, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    tz2 = mixed.tzinfo
    print(tz2 is not None and isinstance(tz2, timezone))


main()
