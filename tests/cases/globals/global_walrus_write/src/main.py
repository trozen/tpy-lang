# A walrus targeting a `global`-declared name writes the MODULE variable, not a
# function-local shadow: the write must be visible to other functions and to a
# later module-level read (CPython parity). Wider target shapes (method
# receiver, float/bool/str, Optional narrowing) live in global_walrus_shapes.

counter = 0
limit = 0
seen = 0


def observe() -> int:
    return counter


def store() -> int:
    global counter
    v = (counter := 7)  # tpyc: ok
    # The same function reads the module variable back, not a shadow.
    return v + counter


def loop_until() -> int:
    global limit
    while (limit := limit + 1) < 4:  # tpyc: ok
        pass
    return limit


def then_plain() -> None:
    global counter
    x = (counter := 10)
    counter = x + 5


def in_comprehension() -> int:
    # PEP 572: a comprehension walrus binds in the containing scope, honouring
    # its `global` declaration.
    global seen
    xs = [(seen := i) for i in range(3)]
    return len(xs)


def main() -> None:
    print("store:", store())
    print("observed:", observe())
    print("loop:", loop_until(), limit)
    then_plain()
    print("after plain:", counter)
    print("comprehension:", in_comprehension(), seen)


main()
