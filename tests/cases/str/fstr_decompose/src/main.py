# FStr: f-string decomposition via call macro, FStr inlining, tuple dispatch,
# static string detection, and deferred-copy string wrapping.
from tpy import FStr, Int32, inline
from log_infra import LogHandle
from log_macro import log_debug


# @inline on a free function
@inline
def log_free(logger: LogHandle, fs: FStr) -> None:
    log_debug(logger, fs)


class Module:
    _logger: LogHandle

    def __init__(self, name: str) -> None:
        self._logger = LogHandle(name)

    # @inline on a method
    @inline
    def log(self, fs: FStr) -> None:
        log_debug(self._logger, fs)


def main() -> None:
    m = Module("M")

    # Method @inline: mixed types
    s = "hello"
    i: Int32 = 42
    m.log(f"s={s} i={i}")

    # Method @inline: static string literal
    m.log(f"status={"ok"}")

    # Method @inline: ternary of string literals
    flag = True
    m.log(f"result={"yes" if flag else "no"}")

    # Method @inline: dynamic string variable
    tag = "world"
    m.log(f"tag={tag}")

    # Free function @inline
    h = LogHandle("F")
    log_free(h, f"free={i}")

main()
