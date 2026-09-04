# A comprehension passed to a builtin container/view method: the stmt-expr
# renders inline into the stub's slot, structural (Iterable) and concrete alike.


def extend_iterable() -> int:
    primes: list[int] = [2, 3]
    # Structural `Iterable[T]` slot: the comprehension binds the template bare.
    primes.extend([i for i in range(5, 12, 2) if i != 9])  # tpyc: ok
    return len(primes)


def join_iterable() -> str:
    xs = [1, 2, 3]
    # The view family's slot, same inline render.
    return ",".join([str(x) for x in xs])  # tpyc: ok


def update_dict() -> int:
    d = {1: 2}
    # A CONCRETE container slot whose element spells `Own[V]`: the target is
    # the comprehension's own container, not the ownership-carrying slot.
    d.update({k: k for k in range(3)})  # tpyc: ok
    return len(d)


def update_set() -> int:
    s = {1, 2}
    s.update({x * 2 for x in range(3)})  # tpyc: ok
    return len(s)


def main():
    print(extend_iterable(), join_iterable(), update_dict(), update_set())


main()
