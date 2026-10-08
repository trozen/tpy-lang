# sys module: argv requires runtime init (not native_module)
# tpy: cpp_namespace("tpystd::sys")
from typing import Final
from tpy import int32, dispatch
from tpy.extern import native


@native("tpy::get_sys_argv")
def _get_sys_argv() -> list[str]: ...


argv: list[str] = _get_sys_argv()

# CPython's value on 64-bit platforms (2**63 - 1). TPy targets 64-bit;
# a 32-bit target would need a per-target constant.
maxsize: Final[int] = 9223372036854775807

# TPy targets little-endian (x86-64 / ARM64), same assumption as maxsize.
byteorder: Final[str] = "little"

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
