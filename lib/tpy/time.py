# tpy: native_module
# tpy: cpp_namespace("tpystd::time")
# tpy: include("<tpy/system.hpp>")
# tpy: include("<tpy/stdlib/time.hpp>")
from tpy import Int64
from tpy.extern import native

# Wall-clock time and sleep
@native("tpy::time_time")
def time() -> float: ...

@native("tpy::stdlib::time::time_ns")
def time_ns() -> Int64: ...

@native("tpy::time_sleep")
def sleep(seconds: float) -> None: ...

# Monotonic clock (use for elapsed-time measurements)
@native("tpy::stdlib::time::perf_counter")
def perf_counter() -> float: ...

@native("tpy::stdlib::time::perf_counter_ns")
def perf_counter_ns() -> Int64: ...

@native("tpy::stdlib::time::monotonic")
def monotonic() -> float: ...

@native("tpy::stdlib::time::monotonic_ns")
def monotonic_ns() -> Int64: ...

# Process CPU time
@native("tpy::stdlib::time::process_time")
def process_time() -> float: ...
