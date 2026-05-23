# Generic-base branch: a base inherits from a generic record whose
# base template has `del_suppresses_default_ctor` (Box / Rc / Weak).
# Even though the instantiation's concrete type args are default-
# ctorable, the template-level del-suppression still makes Base() = default
# implicitly deleted. Subclass without super() must be rejected.
from tpy import Int32
from tplib.box import Box


class Base(Box[Int32]):
    label: str

    def __init__(self, v: Int32, label: str) -> None:
        super().__init__(v)
        self.label = label


class Child(Base):
    extra: Int32

    def __init__(self, v: Int32, label: str, e: Int32) -> None:   # tpyc: error(/must call 'super\(\).__init__/)
        self.extra = e


def main() -> None:
    c = Child(42, "x", 7)
    print(c.extra)


main()
