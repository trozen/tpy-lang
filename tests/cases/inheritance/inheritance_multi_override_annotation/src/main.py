# @override annotations resolve via MRO across all multi-base parents.
# The child overrides foo (from A) and bar (from B); both annotations find
# their target via _find_mro_ancestor_with_method walking record.mro_ancestors.
from typing import override
from tpy import Int32


class A:
    def foo(self) -> str:
        return "A.foo"


class B:
    def bar(self) -> str:
        return "B.bar"


class Both(A, B):
    @override
    def foo(self) -> str:  # tpyc: warning(/non-polymorphic/)
        return "Both.foo"

    @override
    def bar(self) -> str:  # tpyc: warning(/non-polymorphic/)
        return "Both.bar"


def main() -> None:
    b = Both()
    print(b.foo())
    print(b.bar())


main()
