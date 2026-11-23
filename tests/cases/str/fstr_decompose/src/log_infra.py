__tpy__ = {"mod": "native"}
# Logging infrastructure: native types, wrappers, and dispatch.
from tpy.extern import native
from tpy import Own


@native("mylog::LogHandle")
class LogHandle:
    @native("mylog::LogHandle")
    def __init__(self, name: str) -> None: ...

@native("mylog::DeferredStr")
class DeferredStr:
    pass

@native("mylog::StaticStr")
class StaticStr:
    pass

@native("mylog::defer_str")
def defer_str(s: str) -> Own[DeferredStr]: ...

@native("mylog::static_str")
def static_str(s: str) -> Own[StaticStr]: ...

@native("mylog::log_dispatch")
def log_dispatch[T](handle: LogHandle, fmt: str, args: T) -> None: ...
