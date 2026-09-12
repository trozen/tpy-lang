# Regression: aug-assign (+=, -=, etc.) on a value-Optional field whose
# storage is std::optional<T> but whose sema type has been narrowed by
# `if self.f is None: return` / `if self.f is not None:`. The LHS is
# read-then-written so the synthesized binop needs the inner T, not the
# raw std::optional<T>. Covers BigInt (int) and int32 (fixed int).
from tpy import int32


class Counter:
    big: int | None
    small: int32 | None

    def __init__(self, big: int | None, small: int32 | None) -> None:
        self.big = big
        self.small = small

    def step(self) -> None:
        if self.big is None:
            return
        if self.small is None:
            return
        self.big += 1
        self.small += 1
        self.big -= 2
        self.small *= 3
        # `//=` takes the cpp_op fallback path rather than the resolved
        # binop_result -- regression guard for that branch.
        self.big //= 2

    def show(self) -> None:
        print(self.big, self.small)


def main() -> None:
    c = Counter(10, 4)
    c.step()
    c.show()
    none_counter = Counter(None, None)
    none_counter.step()
    print(none_counter.big is None, none_counter.small is None)


main()
