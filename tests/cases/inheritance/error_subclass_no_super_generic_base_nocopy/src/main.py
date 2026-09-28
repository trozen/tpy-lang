# A `@dataclass` child, which never calls the parent's `__init__`, over a base
# on ctor-less `Box[int32]` (LANGUAGE_FEATURES "Single class inheritance").
from dataclasses import dataclass
from tpy import int32
from tplib.box import Box


class Base(Box[int32]):
    label: str

    def __init__(self, v: int32, label: str) -> None:
        super().__init__(v)
        self.label = label


# The synthesized `__eq__` / `__repr__` hide Box's; @dataclass cannot opt out.
@dataclass
class Child(Base):  # tpyc: warning(/'Child.__eq__' hides 'Box.__eq__'/) warning(/'Child.__repr__' hides 'Box.__repr__'/) error(/cannot be constructed without arguments \(ancestor 'Box' \(inherited by 'Base'\)/)
    extra: int32


def main() -> None:
    c = Child(7)
    print(c.extra)


main()
