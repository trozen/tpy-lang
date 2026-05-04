# time module: perf_counter / monotonic / time_ns / process_time.
# Absolute timing values vary, so the test asserts invariants
# (monotonicity, post-2024 epoch sanity) rather than fixed outputs.
import time
from tpy import Int64

def main() -> None:
    p1: float = time.perf_counter()
    p2: float = time.perf_counter()
    print("perf_monotonic:", p2 >= p1)

    m1: float = time.monotonic()
    m2: float = time.monotonic()
    print("monotonic_monotonic:", m2 >= m1)

    pn1: Int64 = time.perf_counter_ns()
    pn2: Int64 = time.perf_counter_ns()
    print("perf_ns_monotonic:", pn2 >= pn1)

    mn1: Int64 = time.monotonic_ns()
    mn2: Int64 = time.monotonic_ns()
    print("monotonic_ns_monotonic:", mn2 >= mn1)

    # 1704067200 = 2024-01-01 UTC seconds; ns is *1e9.
    tn: Int64 = time.time_ns()
    print("time_ns_after_2024:", tn > Int64(1704067200000000000))

    t: float = time.time()
    print("time_after_2024:", t > 1704067200.0)

    cpu: float = time.process_time()
    print("process_time_nonneg:", cpu >= 0.0)
    cpu2: float = time.process_time()
    print("process_time_monotonic:", cpu2 >= cpu)

main()
