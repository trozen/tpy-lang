# tpy: native_module(forward=True)
# tpy: cpp_namespace("tpystd::time")
# tpy: include("<tpy/system.hpp>")
from tpy.extern import native

@native("tpy::time_time")
def time() -> float: ...

@native("tpy::time_sleep")
def sleep(seconds: float) -> None: ...
