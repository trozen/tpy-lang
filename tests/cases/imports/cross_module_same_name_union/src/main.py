# Cross-module same-name identity end-to-end.
#
# - pkg_a and pkg_b both define a record named `Foo`.
# - main.py imports pkg_a.Foo and uses pkg_b.Foo via a pkg_b-local
#   function whose return type is `Own[Foo]` (pkg_b's Foo).
# - A union `pkg_a.Foo | pkg_b.Bar` exercises the cross-module path
#   with non-colliding short names so isinstance narrowing works.
#
# Pre-fix, `NominalType` equality excluded `_module_qname`; the two
# same-name records were indistinguishable in sets / dicts and any
# site comparing types via `==` could silently match the wrong one.
# This test pins the strict qname-aware identity contract end-to-end
# through import, sema, macro expansion, narrowing, and codegen.
from pkg_a import Foo, name_of as name_a
from pkg_b import name_of_b, Bar
from tpy import Int32


def uses_pkg_a(x: Foo) -> Int32:
    return x.val


def describe(u: Foo | Bar) -> str:
    # Union with one member each from pkg_a and pkg_b (different
    # short names so isinstance narrowing is unambiguous).  Pre-fix
    # this compiled the same as today; the test exists to guard
    # against future regressions if someone re-collapses the types.
    if isinstance(u, Foo):
        return name_a(u)
    return "bar"


def main() -> None:
    a = Foo(Int32(42))
    print(uses_pkg_a(a))
    print(name_a(a))
    print(name_of_b())
    print(describe(a))
    print(describe(Bar(Int32(7))))


main()
