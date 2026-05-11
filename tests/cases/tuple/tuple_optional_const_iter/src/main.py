# Const-source iteration of list[tuple[T | None, ...]] passed to a
# const-inferred consumer. Pre-fix this failed at C++ compile because
# tuple_to_pointer<tuple<T*, T*>> rejected the const T* derived from
# iterating a const-bound source. The fix: const-inference on consume's
# tuple param, plus matching const slots through the conversion helper.
#
# list.append takes per-element ownership (Own[T] with T = tuple), so each
# literal element is moved at last use -- explicit copy() preserves the
# source for further appends.
from tpy import Int32, copy


class T:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def consume(p: tuple[T | None, T | None]) -> None:
    a, b = p
    if a is not None:
        print(a.x)


class Holder:
    pairs: list[tuple[T | None, T | None]]
    def __init__(self) -> None:
        self.pairs = []

    def show_all_iter(self) -> None:
        # for it in self.pairs: ... -- const list iter, calls consume
        for it in self.pairs:
            consume(it)

    def show_all_unpack(self) -> None:
        # for a, b in self.pairs: ... -- destructure direct from const source
        for a, b in self.pairs:
            if a is not None:
                print(a.x)


def main() -> None:
    t1 = T(1)
    t2 = T(2)
    h = Holder()
    h.pairs.append((copy(t1), copy(t2)))
    h.pairs.append((t1, None))
    h.pairs.append((None, None))
    h.show_all_iter()
    h.show_all_unpack()


main()
