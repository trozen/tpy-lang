# sys module: argv requires runtime init (not native_module)
# tpy: cpp_namespace("tpystd::sys")
from tpy import Int32
from tpy.extern import native


@native("tpy::get_sys_argv")
def _get_sys_argv() -> list[str]: ...


argv: list[str] = _get_sys_argv()


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
