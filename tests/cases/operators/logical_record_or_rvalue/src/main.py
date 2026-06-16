# `and`/`or` over a record param (borrow form) and a same-type record rvalue
# resolves to the record type (not bool): it compiles, short-circuits, and the
# result aliases the chosen operand.


class Box:
    n: int

    def __init__(self, n: int, log: list[int]) -> None:
        log.append(1)  # observable: increments only when this Box is constructed
        self.n = n


def or_truthy_skips_ctor(a: Box, log: list[int]) -> None:
    # a is always truthy (no __bool__) -> Box(9, log) is NOT constructed
    c = a or Box(9, log)  # tpyc: type(/Box/)
    c.n = 99              # mutate via result -> reaches a (reference semantics)
    print(c.n, a.n, len(log))


def and_truthy_returns_ctor(a: Box, log: list[int]) -> int:
    # a truthy -> `and` returns the RHS, so Box(5, log) IS constructed
    c = a and Box(5, log)  # tpyc: type(/Box/)
    return c.n


def main() -> None:
    seed: list[int] = []
    log1: list[int] = []
    or_truthy_skips_ctor(Box(3, seed), log1)  # 99 99 0
    log2: list[int] = []
    print(and_truthy_returns_ctor(Box(3, seed), log2), len(log2))  # 5 1


main()
