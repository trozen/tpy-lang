# Documented rejects-valid divergence: CPython's time.fromisoformat
# returns an AWARE time for an offset suffix; TPy's time is naive-only
# (aware time deferred), so the offset suffix raises ValueError loudly
# instead of silently dropping the offset. no_cpython: CPython succeeds
# here by design, so the outputs cannot byte-compare.
from datetime import time


def main() -> None:
    try:
        print(time.fromisoformat("14:30+05:00"))
    except ValueError:
        print("ValueError-aware-time-deferred")
    try:
        print(time.fromisoformat("14:30:15.5Z"))
    except ValueError:
        print("ValueError-aware-time-deferred")
    # The naive grammar keeps working right up to the offset.
    print(time.fromisoformat("14:30:15.5"))


main()
