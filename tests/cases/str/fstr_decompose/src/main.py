# f-string decomposition via call macros: explicit logger, wrapper macros
# (composed and chained), tuple dispatch, static strings, deferred copies.
from tpy import int32
from log_infra import LogHandle
from log_macro import log_debug, log
from log_wrap import log_free, log_free_chain


# _logger field path
class Module:
    _logger: LogHandle

    def __init__(self, name: str) -> None:
        self._logger = LogHandle(name)

    # Explicit logger inside a method
    def log_explicit(self, n: int32) -> None:
        log_debug(self._logger, f"explicit={n}")  # tpyc: ok

    # Auto-discover via _logger field
    def log_auto(self, tag: str, n: int32) -> None:
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
def log_from_module(mod: Module, val: int32) -> None:
    log(f"free_mod={val}")


# Free function: log() inspects first param for get_logger() method
def log_from_service(svc: Service, val: int32) -> None:
    log(f"free_svc={val}")


def main() -> None:
    m = Module("M")

    # Explicit logger: mixed types
    s = "hello"
    i: int32 = 42
    log_debug(m._logger, f"s={s} i={i}")

    # Explicit logger: static string literal
    log_debug(m._logger, f"status={"ok"}")

    # Explicit logger: ternary of string literals
    flag = True
    log_debug(m._logger, f"result={"yes" if flag else "no"}")

    # Explicit logger: dynamic string variable
    tag = "world"
    log_debug(m._logger, f"tag={tag}")

    m.log_explicit(5)

    # A wrapper macro composed in Python, positional and by keyword
    h = LogHandle("F")
    log_free(h, f"free={i}")  # tpyc: ok
    log_free(logger=h, fs=f"kw={i}")  # tpyc: ok

    # A wrapper macro expanding to the log_debug macro's call
    log_free_chain(h, f"chain={i}")  # tpyc: ok

    # Auto-discover _logger field in method
    m.log_auto("ctx", 99)

    # Auto-discover get_logger() method in method
    svc = Service("S")
    svc.log_auto("hello")

    # Auto-discover _logger field via free function first param
    log_from_module(m, 77)

    # Auto-discover get_logger() via free function first param
    log_from_service(svc, 88)

    # Plain string to a call macro
    log_debug(m._logger, "plain")

    # Plain string through the wrapper macro
    log_free(h, "free_plain")

    # Plain string with braces (must be escaped in format template)
    log_debug(m._logger, "val={}")

    # Plain string to direct call macro
    log_debug(h, "direct_plain")

    # Plain string with braces to direct call macro
    log_debug(h, "direct_val={}")

main()
