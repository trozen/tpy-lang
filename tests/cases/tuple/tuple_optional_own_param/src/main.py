# User-defined function with Own[tuple[T | None, ...]] param. Two halves:
#
# 1. Call site: gen_call_arg's Own[tuple] branch lifts pointer-form rvalue
#    tuples to storage form via tuple_to_storage. Storage-form sources
#    (e.g. `pairs[0]`) skip the wrap.
# 2. In-body access: the Own[tuple] param is itself a storage-form source
#    (recognized by is_storage_form_source). Destructure goes through
#    tuple_to_pointer; int-literal subscript through std::get + an
#    optional_to_ptr lift on each pointer-repr Optional element.
from tpy import Int32, Own


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def take(t: Own[tuple[P | None, P | None]]) -> Int32:
    a, b = t
    if a is not None and b is not None:
        return a.x + b.x
    if a is not None:
        return a.x
    return Int32(0)


def take_subscript(t: Own[tuple[P | None, P | None]]) -> Int32:
    first = t[0]
    if first is not None:
        return first.x
    return Int32(0)


def main() -> None:
    a = P(1)
    b = P(2)

    print(take((a, b)))
    print(take((a, None)))
    print(take((None, None)))

    pairs: list[tuple[P | None, P | None]] = [(a, b)]
    print(take(pairs[0]))

    print(take_subscript((a, b)))
    print(take_subscript((None, b)))
    print(take((None, b)))


main()
