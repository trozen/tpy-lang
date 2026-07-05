# datetime v4 aware arithmetic and cross-zone comparison: adding a
# timedelta is WALL-CLOCK arithmetic (components shift, the offset is
# re-derived per instant, real elapsed time differs across a DST edge),
# subtraction and == reduce through real UTC instants, and a fixed-offset
# aware value mixes with a ZoneInfo aware value by instant.
from datetime import datetime, timedelta, timezone, UTC
from zoneinfo import ZoneInfo


def main() -> None:
    waw = ZoneInfo("Europe/Warsaw")
    ny = ZoneInfo("America/New_York")

    # Across the spring-forward edge: wall +2h, elapsed only 1h.
    before = datetime(2023, 3, 26, 1, 30, tzinfo=waw)
    after = before + timedelta(hours=2)
    print(after, after.utcoffset(), after.fold)
    print((after - before), (after.timestamp() - before.timestamp()))

    # Arithmetic keeps the zone; the result re-resolves its offset.
    day_later = before + timedelta(days=1)
    print(day_later, day_later.utcoffset())
    print(day_later - timedelta(days=1) == before)

    # Arithmetic from a fold=1 value drops fold to 0 on the result
    # (CPython constructs the sum without propagating fold).
    folded = datetime(2023, 10, 29, 2, 30, fold=1, tzinfo=waw)
    moved = folded + timedelta(hours=1)
    print(moved, moved.fold)
    print((folded - timedelta(0)).fold)

    # Cross-zone subtraction and equality are instant-based.
    w = datetime(2023, 7, 15, 12, 0, tzinfo=waw)
    n = datetime(2023, 7, 15, 12, 0, tzinfo=ny)
    print(w - n)
    print(w == n, w < n)
    print(w == datetime(2023, 7, 15, 6, 0, tzinfo=ny))

    # Fixed-offset vs ZoneInfo mixing: same instant compares equal.
    rey = ZoneInfo("Atlantic/Reykjavik")
    print(datetime(2023, 7, 15, 12, 0, tzinfo=UTC)
          == datetime(2023, 7, 15, 12, 0, tzinfo=rey))
    fixed2 = timezone(timedelta(hours=2))
    print(datetime(2023, 7, 15, 12, 0, tzinfo=fixed2)
          == datetime(2023, 7, 15, 12, 0, tzinfo=waw))
    print(datetime(2023, 7, 15, 12, 0, tzinfo=fixed2) - w)

    # Naive/aware mixing stays a runtime TypeError for ordering/subtraction
    # and False for ==, with the zoneinfo kind too.
    naive = datetime(2023, 7, 15, 12, 0)
    print(naive == w)
    try:
        print(naive < w)
    except TypeError:
        print("TypeError-order")
    try:
        print(naive - w)
    except TypeError:
        print("TypeError-sub")


main()
