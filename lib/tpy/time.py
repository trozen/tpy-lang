# tpy: native_module
# tpy: cpp_namespace("tpystd::time")
# tpy: include("<tpy/system.hpp>")
# tpy: include("<tpy/stdlib/time.hpp>")
from tpy import int64
from tpy.extern import native

# Wall-clock time and sleep
# Reads the system clock and nothing else.
@native("tpy::time_time", transient=True)
def time() -> float: ...

@native("tpy::stdlib::time::time_ns")
def time_ns() -> int64: ...

@native("tpy::time_sleep")
def sleep(seconds: float) -> None: ...

# Sleep until a steady_clock deadline expressed in seconds (same domain
# as monotonic()). Used by asyncio's run loop to wait for the next
# timer; user code should generally prefer sleep().
@native("tpy::stdlib::time::sleep_until_steady")
def sleep_until_steady(deadline_seconds: float) -> None: ...

# Monotonic clock (use for elapsed-time measurements)
@native("tpy::stdlib::time::perf_counter")
def perf_counter() -> float: ...

@native("tpy::stdlib::time::perf_counter_ns")
def perf_counter_ns() -> int64: ...

@native("tpy::stdlib::time::monotonic")
def monotonic() -> float: ...

@native("tpy::stdlib::time::monotonic_ns")
def monotonic_ns() -> int64: ...

# Process CPU time
@native("tpy::stdlib::time::process_time")
def process_time() -> float: ...

# CPython-parity no-op: TPy's local-time provider reads TZ once at first
# use, so set-TZ-then-tzset-then-use works; a tzset after local time was
# already used cannot re-pin the zone (documented divergence).
@native("tpy::stdlib::time::tzset")
def tzset() -> None: ...
