# The generic-record CONSTRUCTOR seam's no-flush reject: a view-form `str` at
# the ctor's `T` slot needs a materialized owned temp, and a while condition has
# no statement to flush one into. The free-call seam's twin is
# `generics/error_generic_slot_view_no_flush`, the method's is `..._method`.
# This REJECTS valid Python; the divergence is the documented one in
# docs/LANGUAGE_FEATURES.md ("no statement to hoist the temp into").
class Labels[T]:
    items: list[T]

    def __init__(self, first: T) -> None:
        self.items = [first]

    def has(self, value: T) -> bool:
        for it in self.items:
            if it == value:
                return True
        return False


def main(k: str) -> None:
    n = 0
    # The subject: the ctor's generic slot cannot hoist its temp here.
    while Labels[str](k).has("z") or n < 1:  # tpyc: error(/ctor.generic_slot_view_no_flush/)
        n += 1
    print(n)


main("a")
