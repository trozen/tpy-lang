# Generator yielding tuple[T | None, ...] with VALUE-type Optional elements
# (int32 | None). uses_pointer_repr() is False, so has_pointer_repr_optional_element()
# returns False and the storage-form lift does NOT fire -- value-type Optionals
# already live in std::optional<int32_t> regardless of context. Documents the
# negative case of the gating predicate on the tuple yield's storage-form lift.
from typing import Iterator
from tpy import int32


def gen_value_pairs(items: list[int32]) -> Iterator[tuple[int32 | None, int32 | None]]:
    for it in items:
        yield (it, None)


def main() -> None:
    items = [int32(1), int32(2), int32(3)]
    for a, b in gen_value_pairs(items):
        if a is not None:
            print(a)


main()
