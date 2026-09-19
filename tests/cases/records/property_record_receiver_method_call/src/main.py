# A RECORD-valued @property as a METHOD receiver. The read is a getter call, so
# `c.p.bump()` composes over it the way `c.get_p().bump()` always did; it used to
# reject (method.recv.field_parent) because the receiver wore a field access's
# node kind.
# The ALIAS is the subject: the getter returns a borrow of the stored record, so
# the mutation must be visible when the owner is re-read through a second path.
# The container-valued sibling is pinned by tests/cases/records/property_ref_semantics.
from tpy import int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def bump(self) -> None:
        self.x += 1


class C:
    _p: P

    def __init__(self) -> None:
        self._p = P(1)

    @property
    def p(self) -> P:
        return self._p


# free function: mutate through the getter receiver, read back through the field
def use_record(c: C) -> None:
    c.p.bump()  # tpyc: ok
    c.p.bump()  # tpyc: ok
    print("free_fn:", c._p.x)


class Owner:
    c: C

    def __init__(self) -> None:
        self.c = C()

    # method body: the same receiver one hop out
    def bump_twice(self) -> None:
        self.c.p.bump()  # tpyc: ok
        self.c.p.bump()  # tpyc: ok


def main() -> None:
    c = C()
    use_record(c)
    o = Owner()
    o.bump_twice()
    print("method:", o.c._p.x)


main()
