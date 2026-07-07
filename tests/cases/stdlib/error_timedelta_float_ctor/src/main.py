# A float timedelta constructor component (timedelta(hours=1.5)) is rejected:
# the components are integer-only. Fractional durations use integer components
# (timedelta(minutes=90)) or the rounding operators (timedelta(hours=1) / 2).
from datetime import timedelta


def main() -> None:
    d = timedelta(hours=1.5)  # tpyc: error(/expected int, got float/)
    print(d)


main()
