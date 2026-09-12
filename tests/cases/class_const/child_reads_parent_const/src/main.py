# Phase 6: `Child.X` resolves through `mro_ancestors` to the declaring
# ancestor; codegen emits `<Parent>::<X>` regardless of which class the user
# names. Combined with Phase 5, instance-side reads through a child also work.
from typing import Final
from tpy import int32


class Parent:
    LIMIT: Final[int32] = 10


class Child(Parent):
    pass


def main() -> None:
    # Direct access via the child class.
    print(Child.LIMIT)
    # The parent name still resolves to the same constant.
    print(Parent.LIMIT)
    # Instance-side read through a Child instance (Phase 5 + 6).
    c = Child()
    print(c.LIMIT)


main()
