# Phase 5: instance-side reads of class constants. `obj.X` falls back to the
# class's class_constants; codegen emits `<Class>::<X>` (the instance is a
# carrier only).
from typing import Final
from tpy import int32


class C:
    LIMIT: Final[int32] = 10
    NAME: Final[str] = "C"

    def __init__(self) -> None:
        pass

    def show(self) -> None:
        # Read class constants through `self`.
        print(self.LIMIT)
        print(self.NAME)


def main() -> None:
    c = C()
    print(c.LIMIT)
    print(c.NAME)
    c.show()


main()
