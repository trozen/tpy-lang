# Array literal with non-copyable union elements.
# std::array uses aggregate init (no initializer_list), so move-only
# types should work with plain brace-init -- no make_vector needed.
from dataclasses import dataclass
from tpy import Int32, nocopy, Array


@nocopy
@dataclass
class Heavy:
    value: Int32


@dataclass
class Light:
    value: Int32


type Item = Heavy | Light


def main() -> None:
    items: Array[Item, 3] = [Heavy(1), Light(2), Heavy(3)]
    print(len(items))

    uniform: Array[Heavy, 2] = [Heavy(10), Heavy(20)]
    print(len(uniform))

main()
