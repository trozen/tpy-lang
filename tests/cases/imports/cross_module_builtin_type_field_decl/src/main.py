# Smoke test: cross-module field-decl + constructor for a generic
# `@builtin_type` record. `Poll` is decorated
# `@builtin_type("tpy.coro.Poll")` -- its body lives in
# `tpy/_core/_types.py` for codegen-ordering reasons, but the TPy qname
# matches the user-facing import path `tpy.coro.Poll` (the package
# re-exports). The field annotation and the constructor expression must
# resolve to the same NominalType -- handled by `_user_record_qname`'s
# `builtin_type_key` precedence at the generic resolution path. Without
# that precedence, sema would fire "Type mismatch in assignment:
# expected Poll, got Poll" with differing `_module_qname`.
from tpy.coro import Poll
from tpy import Int32, Own


class Holder:
    last: Own[Poll[Int32]]

    def __init__(self) -> None:
        self.last = Poll[Int32].pending()  # tpyc: ok type(Own[Poll[Int32]])


def main() -> None:
    h = Holder()
    print(h.last.is_pending())


main()
