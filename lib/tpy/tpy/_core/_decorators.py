# tpy: native_module
# tpy: cpp_namespace("tpystd::tpy")
# Decorator and type modifier stubs for import resolution.
# Type modifier stubs use @builtin_type so the compiler knows their
# qualified name (e.g. "tpy.Own"). Codegen skips them via is_keyword_stub.
# Decorator stubs use @builtin_decorator so the compiler resolves them
# by qualified name (e.g. "tpy.readonly"). Codegen skips via is_decorator_stub.
# Stub signatures define argument schemas -- the parser derives validation
# rules from params (type and default) instead of hardcoding them.
from ._extern import builtin_type, builtin_decorator

@builtin_type("tpy.Own")
class Own: ...

@builtin_type("tpy.Fn")
class Fn: ...

@builtin_decorator("tpy.readonly")
def readonly(is_readonly: bool = True): ...

@builtin_decorator("tpy.noalloc")
def noalloc(): ...

@builtin_decorator("tpy.nocopy")
def nocopy(): ...

@builtin_decorator("tpy.dynamic")
def dynamic(): ...

@builtin_decorator("tpy.pure")
def pure(): ...

@builtin_decorator("tpy.error_return")
def error_return(exc_type: type): ...
