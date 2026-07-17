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

@builtin_decorator("tpy.auto_readonly")
def auto_readonly(): ...

@builtin_decorator("tpy.noalloc")
def noalloc(): ...

@builtin_decorator("tpy.hotpath")
def hotpath(): ...

@builtin_decorator("tpy.nocopy")
def nocopy(): ...

@builtin_decorator("tpy.dynamic")
def dynamic(): ...

@builtin_decorator("tpy.pure")
def pure(): ...

@builtin_decorator("tpy.inline")
def inline(): ...

@builtin_decorator("tpy.error_return")
def error_return(exc_type: type): ...

# Bare (`@unsafe_send`) forces the trait True unconditionally. The optional
# if_params_send / if_params_sync kwargs (generic classes only) make it
# conditional: the record is Send/Sync at an instantiation iff every type param
# satisfies the flagged markers -- e.g. `@unsafe_send(if_params_send=True,
# if_params_sync=True)` on Arc. Args are custom-parsed by the parser.
@builtin_decorator("tpy.unsafe_send")
def unsafe_send(if_params_send: bool = False, if_params_sync: bool = False): ...

@builtin_decorator("tpy.unsafe_sync")
def unsafe_sync(if_params_send: bool = False, if_params_sync: bool = False): ...

@builtin_decorator("tpy.nosend")
def nosend(): ...

@builtin_decorator("tpy.nosync")
def nosync(): ...

@builtin_decorator("tpy.nomove")
def nomove(): ...
