# tpy: native_module
# super() is a compile-time construct in TPy (no runtime super-proxy object);
# this stub exists only to give `super` a qualified name so sema can resolve
# call-site references through the normal namespace chain instead of matching
# the bare string. A user `def super()` at module/function scope shadows this
# binding the usual way, matching CPython semantics.
from .._bootstrap._extern import builtin_type


@builtin_type("builtins.super")
class super: ...
