# Nested-def with typed `**kwargs: Unpack[TypedDict]` is not yet
# supported in sema (the inner-function body can't resolve `kw` as a
# local). This case is a regression guard for parser-side kwarg_type
# resolution -- `_finalize_function_refs` used to skip `func.kwarg_type`
# (it resolved params + vararg only), leaving the raw TpyTypeRef which
# leaked downstream as "Internal error: 'TpyTypeRef' object has no
# attribute 'map_inner_types'". With kwarg_type resolved at parse-time,
# the user-visible diagnostic is the proper sema error below.
from typing import TypedDict, Unpack


class KW(TypedDict):
    a: int
    b: int


def outer() -> None:
    def inner(**kw: Unpack[KW]) -> None:
        print(kw["a"])  # tpyc: error(/Undefined variable.*kw/)
    inner(a=1, b=2)


outer()
