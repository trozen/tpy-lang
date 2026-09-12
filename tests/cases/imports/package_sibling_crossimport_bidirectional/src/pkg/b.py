from tpy import int32

# Bidirectional sibling cross-import: pkg.a imports B from us AND we
# import A from pkg.a. Both directions auto-walk back to pkg in their
# generated headers, so every sibling re-export in pkg.hpp would
# otherwise hit the parent<->sub include cycle. Cycle members may only
# contain imports and bare type declarations at top level -- no
# executable statements, and they cannot store each other by value.
from pkg.a import A


class B:
    def kind(self) -> int32:
        return int32(2)


def use_a(a: A) -> int32:
    return a.kind()
