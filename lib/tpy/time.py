# tpy: native_module
from tpy.extern import native

@native("tpy::time_time")
def time() -> float: ...

@native("tpy::time_sleep")
def sleep(seconds: float) -> None: ...
