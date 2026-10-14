# sys module: argv requires runtime init (not native_module)
__tpy__ = {"cpp_namespace": "tpystd::sys",
           "includes": ["<tpy/platform.hpp>"]}
from typing import Final
from tpy import int32, dispatch
from tpy.extern import native, native_global


@native("tpy::get_sys_argv")
def _get_sys_argv() -> list[str]: ...


argv: list[str] = _get_sys_argv()

# The build target's values, decided by its compiler (tpy/platform.hpp):
# a cross build answers for the machine it builds for.
platform: Final[str] = native_global("tpy::sys_platform")
byteorder: Final[str] = native_global("tpy::sys_byteorder")
maxsize: Final[int] = native_global("tpy::sys_maxsize")

# U+10FFFF -- fixed by the Unicode standard, so target-independent (unlike
# maxsize / byteorder).
maxunicode: Final[int] = 0x10FFFF


@native("tpy::StdStream")
class _StdStream:
    @native("write", checks_signals=True)
    def write(self, text: str) -> int32: ...

    @native("flush", checks_signals=True)
    def flush(self) -> None: ...


@native("tpy::get_sys_stdout")
def _get_sys_stdout() -> _StdStream: ...


@native("tpy::get_sys_stderr")
def _get_sys_stderr() -> _StdStream: ...


stdout: _StdStream = _get_sys_stdout()
stderr: _StdStream = _get_sys_stderr()


# TODO: declare as ``NoReturn`` once TPy gains the type; until then code
# after a ``sys.exit()`` is not flagged as dead.
# The per-alternative variants: BUGS.md#dispatch-union-param-refuses-literal,
# BUGS.md#bigint-arg-to-int-union-param-ill-formed.
@dispatch
def exit() -> None:
    raise SystemExit()


@dispatch
def exit(code: int32) -> None:
    raise SystemExit(code)


@dispatch
def exit(code: str) -> None:
    raise SystemExit(code)


@dispatch
def exit(code: None) -> None:
    # CPython's sys.exit(None) raises with args () and str '', unlike an
    # explicit SystemExit(None).
    raise SystemExit()


@dispatch
def exit(code: int32 | str | None) -> None:
    if isinstance(code, int32):
        raise SystemExit(code)
    if isinstance(code, str):
        raise SystemExit(code)
    raise SystemExit()
