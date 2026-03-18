# sys module: argv requires runtime init (not native_module)
from tpy.extern import native

@native("tpy::get_sys_argv")
def _get_sys_argv() -> list[str]: ...

argv: list[str] = _get_sys_argv()
