# Container literals first declared inside branches (if/else, try/except,
# with, loop body) resolve via the finalized branch-decl snapshot.
def pick_list(c: bool) -> int:
    if c:
        xs = [1, 2]
    else:
        xs = [3]
    xs.append(9)
    return xs[0] + len(xs)


def pick_dict(c: bool) -> int:
    if c:
        d = {"a": 1}
    else:
        d = {"b": 2, "c": 3}
    return len(d)


def pick_set(c: bool) -> int:
    if c:
        s = {1, 2}
    else:
        s = {3}
    return len(s)


class Mgr:
    def __enter__(self) -> None:
        pass

    def __exit__(self, a: None, b: None, c: None) -> None:
        pass


def pick_try(c: bool) -> int:
    try:
        if c:
            raise ValueError("x")
        xs = [1, 2]
    except ValueError:
        xs = [3]
    return xs[0] + len(xs)


def pick_with() -> int:
    with Mgr():
        xs = [1, 2]
    xs.append(8)
    return xs[0] + len(xs)


def pick_loop() -> int:
    for i in range(3):
        xs = [1, i]
    return xs[0] + len(xs)


def main() -> None:
    print(pick_list(True))
    print(pick_list(False))
    print(pick_dict(True))
    print(pick_dict(False))
    print(pick_set(True))
    print(pick_set(False))
    print(pick_try(False))
    print(pick_try(True))
    print(pick_with())
    print(pick_loop())


main()
