# FStr: f-string decomposition via call macro, FStr inlining, tuple dispatch,
# static string detection, deferred-copy string wrapping, and auto-discovery.
from tpy import FStr, Int32, inline
from log_infra import LogHandle
from log_macro import log_debug, log


# @inline on a free function
@inline
def log_free(logger: LogHandle, fs: FStr) -> None:
    log_debug(logger, fs)


# _logger field path
class Module:
    _logger: LogHandle

    def __init__(self, name: str) -> None:
        self._logger = LogHandle(name)

    # @inline on a method: explicit logger
    @inline
    def log_inline(self, fs: FStr) -> None:
        log_debug(self._logger, fs)

    # Auto-discover via _logger field
    def log_auto(self, tag: str, n: Int32) -> None:
        log(f"tag={tag} n={n}")


# get_logger() method path
class Service:
    _handle: LogHandle

    def __init__(self, name: str) -> None:
        self._handle = LogHandle(name)

    def get_logger(self) -> LogHandle:
        return self._handle

    # Auto-discover via get_logger() method
    def log_auto(self, msg: str) -> None:
        log(f"svc={msg}")


# Free function: log() inspects first param for _logger field
def log_from_module(mod: Module, val: Int32) -> None:
    log(f"free_mod={val}")


# Free function: log() inspects first param for get_logger() method
def log_from_service(svc: Service, val: Int32) -> None:
    log(f"free_svc={val}")


def main() -> None:
    m = Module("M")

    # Method @inline: mixed types
    s = "hello"
    i: Int32 = 42
    m.log_inline(f"s={s} i={i}")

    # Method @inline: static string literal
    m.log_inline(f"status={"ok"}")

    # Method @inline: ternary of string literals
    flag = True
    m.log_inline(f"result={"yes" if flag else "no"}")

    # Method @inline: dynamic string variable
    tag = "world"
    m.log_inline(f"tag={tag}")

    # Free function @inline
    h = LogHandle("F")
    log_free(h, f"free={i}")

    # Auto-discover _logger field in method
    m.log_auto("ctx", 99)

    # Auto-discover get_logger() method in method
    svc = Service("S")
    svc.log_auto("hello")

    # Auto-discover _logger field via free function first param
    log_from_module(m, 77)

    # Auto-discover get_logger() via free function first param
    log_from_service(svc, 88)

    # Plain string to @inline FStr parameter
    m.log_inline("plain")

    # Plain string to @inline free function FStr parameter
    log_free(h, "free_plain")

    # Plain string with braces (must be escaped in format template)
    m.log_inline("val={}")

    # Plain string to direct call macro (not via @inline)
    log_debug(h, "direct_plain")

    # Plain string with braces to direct call macro
    log_debug(h, "direct_val={}")

main()
