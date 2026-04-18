# Tests for time.perf_counter() and time.monotonic()
import time

def main() -> None:
    # perf_counter returns a positive float
    t1 = time.perf_counter()
    print(t1 > 0.0)

    # Two successive calls: second >= first (monotonic property)
    t2 = time.perf_counter()
    print(t2 >= t1)

    # monotonic returns a positive float
    m1 = time.monotonic()
    print(m1 > 0.0)

    m2 = time.monotonic()
    print(m2 >= m1)

main()
