# datetime v4 PEP 495 fold: both fold values at Warsaw's 2023 gap
# (2023-03-26 02:30 does not exist) and fold (2023-10-29 02:30 occurs
# twice) -- exact utcoffset/dst/tzname/timestamp per side, the same-zone
# comparison rule (fold pair is EQUAL, hash-equal, not ordered), fold
# validation, and replace(fold=).
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def main() -> None:
    waw = ZoneInfo("Europe/Warsaw")

    # Constructor never auto-detects fold; fold=1 must be explicit.
    fold_wall = datetime(2023, 10, 29, 2, 30, tzinfo=waw)
    fold_wall1 = fold_wall.replace(fold=1)
    print(fold_wall.fold, fold_wall1.fold)
    print(fold_wall.utcoffset(), fold_wall.dst(), fold_wall.tzname())
    print(fold_wall1.utcoffset(), fold_wall1.dst(), fold_wall1.tzname())
    print(fold_wall.timestamp(), fold_wall1.timestamp())

    gap_wall = datetime(2023, 3, 26, 2, 30, tzinfo=waw)
    gap_wall1 = gap_wall.replace(fold=1)
    print(gap_wall.utcoffset(), gap_wall.dst(), gap_wall.tzname())
    print(gap_wall1.utcoffset(), gap_wall1.dst(), gap_wall1.tzname())
    print(gap_wall.timestamp(), gap_wall1.timestamp())

    # Same-zone pairs compare by wall clock (CPython's same-tzinfo rule):
    # the fold pair is equal and hash-equal despite differing utcoffsets.
    print(fold_wall == fold_wall1, hash(fold_wall) == hash(fold_wall1))
    print(fold_wall < fold_wall1, fold_wall > fold_wall1)
    # ...but subtraction goes through real per-instant offsets.
    print(fold_wall1 - fold_wall)

    # fold is ignored for naive equality/hash too.
    naive = datetime(2023, 10, 29, 2, 30)
    naive1 = naive.replace(fold=1)
    print(naive == naive1, hash(naive) == hash(naive1))
    print(repr(naive1))

    # fold is INERT on a fixed-offset timezone: same offset/dst/name/
    # timestamp at both fold values.
    fixed = datetime(2023, 10, 29, 2, 30,
                     tzinfo=timezone(timedelta(hours=2)))
    fixed1 = fixed.replace(fold=1)
    print(fixed.utcoffset() == fixed1.utcoffset(),
          fixed.dst() is None and fixed1.dst() is None,
          fixed.tzname() == fixed1.tzname(),
          fixed.timestamp() == fixed1.timestamp())
    print(repr(fixed1))

    # replace(tzinfo=<ZoneInfo>) attaches the zone (and keeps fold here).
    rezoned = fixed1.replace(tzinfo=waw)
    print(rezoned, rezoned.fold)

    # fold validation is a catchable ValueError (match type, not text).
    try:
        datetime(2023, 1, 1, fold=2)
        print("no-raise")
    except ValueError:
        print("ValueError-fold")
    try:
        naive.replace(fold=-1)
        print("no-raise")
    except ValueError:
        print("ValueError-replace-fold")


main()
