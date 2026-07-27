# A local first declared inside a for/while/match/try block and then assigned
# AFTER that block is one function-scoped local (a block is not a scope in
# Python), so the post-block assignment must not emit a write to the C++
# block-scoped name.


def in_for() -> int:
    for i in range(3):
        n = i + 1
        print(n)
    n = 9
    return n


def in_while() -> int:
    c = 2
    while c > 0:
        n = c
        print(n)
        c -= 1
    n = 9
    return n


def in_match(tag: int) -> int:
    match tag:
        case 1:
            n = 11
            print(n)
        case _:
            pass
    n = 9
    return n


def in_try(d: int) -> int:
    try:
        n = 100 // d
        print(n)
    except ZeroDivisionError:
        pass
    n = 9
    return n


def in_except(d: int) -> int:
    # The handler body is a block too: a name first bound there and assigned
    # after the try is the same local.
    try:
        print(100 // d)
    except ZeroDivisionError:
        n = -1
        print(n)
    n = 9
    return n


def in_for_else() -> int:
    # A loop `else:` body is its own C++ block (the `break` path jumps past
    # it), so its declarations must not reach the post-loop assignment.
    for i in range(3):
        print(i)
    else:
        n = 7
        print(n)
    n = 9
    return n


def in_while_else(k: int) -> int:
    i = 0
    while i < k:
        print(i)
        i += 1
    else:
        n = 7
        print(n)
    n = 9
    return n


def optional_local(flag: bool) -> str:
    # Optional-typed locals carry extra codegen classification (storage vs
    # pointer form) that the scope snapshot also restores.
    for i in range(2):
        v: str | None = None
        if flag:
            v = str(i)
        print(v is None)
    v = "nine"
    return v


def str_local() -> str:
    for i in range(2):
        s = str(i)
        print(s)
    s = "after"
    return s


def list_local() -> int:
    for i in range(2):
        xs = [i, i]
        print(xs[0])
    xs = [9, 9]
    return xs[0]


def nested_blocks(flag: bool) -> int:
    # Declared in a loop nested inside an if: still one function local.
    if flag:
        for i in range(2):
            n = i + 5
            print(n)
    n = 9
    return n


def main() -> None:
    print(in_for())
    print(in_while())
    print(in_match(1))
    print(in_match(2))
    print(in_try(5))
    print(in_try(0))
    print(in_except(0))
    print(in_for_else())
    print(in_while_else(2))
    print(optional_local(True))
    print(optional_local(False))
    print(str_local())
    print(list_local())
    print(nested_blocks(True))
    print(nested_blocks(False))


main()
