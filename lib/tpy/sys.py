# sys module
# tpy: cpp_namespace("tpystd::sys")
# tpy: include("<cstdlib>")
from typing import Final
from tpy.extern import native
from tpy import Int32, Int64

@native("tpy::get_sys_argv")
def _get_sys_argv() -> list[str]: ...

argv: list[str] = _get_sys_argv()

maxsize: Final[Int64] = 9223372036854775807

platform: Final[str] = "linux"

@native("std::exit")
def _exit_impl(code: Int32) -> None: ...

def exit(code: Int32 = 0) -> None:
    _exit_impl(code)
