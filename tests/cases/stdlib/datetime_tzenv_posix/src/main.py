# datetime v3 TZ backend, POSIX-rule-string arm: TZ="EST5EDT,M3.2.0,M11.1.0"
# is parsed by the provider's POSIX reader (not the tz database), matching
# glibc's handling of rule-string TZ values. Set before any datetime use
# (TPy pins the zone at first use; a mid-run TZ change is a documented
# divergence, so tests use exactly one value). Deterministic: rule strings
# carry their own transitions, no tz-db data dependency.
import os
import time as _time
os.environ["TZ"] = "EST5EDT,M3.2.0,M11.1.0"
_time.tzset()  # CPython needs it; TPy pins TZ at first use (no-op)

from datetime import datetime, timezone, timedelta, UTC


def main() -> None:
    # Winter EST (-5) vs summer EDT (-4), incl. abbreviations from the rule.
    print(repr(datetime(2021, 1, 15, 12, 0).astimezone()))
    print(repr(datetime(2021, 7, 15, 12, 0).astimezone()))
    print(datetime(2021, 1, 15, 12, 0).timestamp())
    print(datetime(2021, 7, 15, 12, 0).timestamp())
    # Second Sunday in March 2021 = Mar 14; 02:30 is in the gap.
    print(datetime(2021, 3, 14, 2, 30).timestamp())
    # First Sunday in November 2021 = Nov 7; 01:30 repeats (fold).
    print(datetime(2021, 11, 7, 1, 30).timestamp())
    print(datetime.fromtimestamp(1626364800.0))
    print(datetime(2021, 7, 15, 12, 0).strftime("%Z %z"))
    print(datetime(2021, 7, 15, 12, 0).astimezone().strftime("%Z %z"))
    print(datetime(2021, 7, 15, 16, 0, tzinfo=UTC).astimezone()
          == datetime(2021, 7, 15, 12, 0).astimezone())


main()
