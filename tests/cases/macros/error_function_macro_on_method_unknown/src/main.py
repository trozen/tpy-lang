# A non-builtin decorator on a method routes to pending_macros (it is no
# longer a parse error), and sema rejects an unregistered one as an unknown
# function macro -- the same UX free functions have. Guards the invariant
# that a macro decorator is never silently dropped on a method.
from badmod import not_a_macro


class C:
    @not_a_macro
    def m(self) -> None:  # tpyc: error(/Unknown function macro/)
        pass


def main() -> None:
    c = C()
    c.m()


main()
