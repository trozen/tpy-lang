# List literal with non-copyable union elements.
# @nocopy makes Heavy move-only, so brace-init (std::initializer_list)
# won't work -- codegen must use make_vector (reserve + emplace_back).
from dataclasses import dataclass
from tpy import int32, nocopy


@nocopy
@dataclass
class Heavy:
    value: int32


@dataclass
class Light:
    value: int32


type Item = Heavy | Light


def main() -> None:
    items: list[Item] = [Heavy(1), Light(2), Heavy(3)]
    print(len(items))

    single: list[Item] = [Light(42)]
    print(len(single))

    empty: list[Item] = []
    print(len(empty))

main()
