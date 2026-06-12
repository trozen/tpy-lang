# sys module: argv requires runtime init (not native_module)
# tpy: cpp_namespace("tpystd::sys")
from typing import Final
from tpy import Int32
from tpy.extern import native


@native("tpy::get_sys_argv")
def _get_sys_argv() -> list[str]: ...


argv: list[str] = _get_sys_argv()

# CPython's value on 64-bit platforms (2**63 - 1). TPy targets 64-bit;
# a 32-bit target would need a per-target constant.
maxsize: Final[int] = 9223372036854775807


@native("tpy::StdStream")
class _StdStream:
    @native("write")
    def write(self, text: str) -> Int32: ...

    @native("flush")
    def flush(self) -> None: ...


@native("tpy::get_sys_stdout")
def _get_sys_stdout() -> _StdStream: ...


@native("tpy::get_sys_stderr")
def _get_sys_stderr() -> _StdStream: ...


stdout: _StdStream = _get_sys_stdout()
stderr: _StdStream = _get_sys_stderr()


# TODO: declare as ``NoReturn`` once TPy gains the type. Today the
# binding is ``-> None``, so sema treats call sites as normal returns
# and won't flag dead code after a ``sys.exit()``. The C++ shim is
# ``[[noreturn]]``, so the runtime behavior is correct.
@native("tpy::sys_exit")
def exit(code: Int32) -> None: ...
