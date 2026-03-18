# sys module: argv requires runtime init (not native_module)
# tpy: cpp_namespace("tpystd::sys")
from tpy.extern import native

@native("tpy::get_sys_argv")
def _get_sys_argv() -> list[str]: ...

argv: list[str] = _get_sys_argv()
