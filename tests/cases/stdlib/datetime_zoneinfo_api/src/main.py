# zoneinfo completion: available_timezones (only host-stable membership/
# exclusion invariants are printed -- the exact set and count depend on
# the host tz database) and ZoneInfo.fromutc incl. PEP 495 fold on the
# second pass of a repeated wall time.
from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones


def main() -> None:
    zones = available_timezones()
    print(len(zones) > 100)
    print("Europe/Warsaw" in zones, "America/New_York" in zones,
          "UTC" in zones)
    print("posixrules" in zones)
    bad_prefix = False
    for k in zones:
        if k.startswith("posix/") or k.startswith("right/"):
            bad_prefix = True
    print(bad_prefix)
    # The completion invariant: every listed key constructs and
    # round-trips its key.
    all_ok = True
    for k in zones:
        z = ZoneInfo(k)
        if z.key != k:
            all_ok = False
    print(all_ok)

    waw = ZoneInfo("Europe/Warsaw")
    # UTC 00:30 and 01:30 on 2023-10-29 are the two passes of the
    # repeated wall time 02:30 in Warsaw.
    r0 = waw.fromutc(datetime(2023, 10, 29, 0, 30, tzinfo=waw))
    r1 = waw.fromutc(datetime(2023, 10, 29, 1, 30, tzinfo=waw))
    print(r0, r0.fold)
    print(r1, r1.fold)
    print(r1.timestamp() - r0.timestamp())
    # Outside any transition: plain shift, fold 0.
    plain = waw.fromutc(datetime(2023, 7, 15, 10, 0, tzinfo=waw))
    print(plain, plain.fold)
    # tzinfo must be this zone: naive and other-zone inputs raise.
    try:
        waw.fromutc(datetime(2023, 10, 29, 0, 30))
        print("no-raise")
    except ValueError:
        print("ValueError-naive")
    try:
        waw.fromutc(datetime(2023, 10, 29, 0, 30,
                             tzinfo=ZoneInfo("America/New_York")))
        print("no-raise")
    except ValueError:
        print("ValueError-other-zone")


main()
