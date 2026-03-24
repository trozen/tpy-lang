# tpy: native_module
# tpy: cpp_namespace("tpystd::tpy")
# Parser keyword stubs: decorators and type modifiers consumed at parse
# time. This file exists for import resolution within _core submodules.
# Decorator names are skipped by sema via is_parser_keyword().
# Type modifier stubs use @builtin_type so the compiler knows their
# qualified name (e.g. "tpy.Own"). Codegen skips them via is_keyword_stub.
from ._extern import builtin_type

@builtin_type("tpy.Own")
class Own: ...

@builtin_type("tpy.Fn")
class Fn: ...
