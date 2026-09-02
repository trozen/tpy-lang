# A record rvalue passed to a reference-typed parameter inside a raise's
# argument must be materialized before the throw line, not rejected as a
# temp at an unflushable position.
class Tag:
    def __init__(self, n: int) -> None:
        self.n = n


class TagError(Exception):
    n: int

    def __init__(self, t: Tag) -> None:
        super().__init__("tag" + str(t.n))
        self.n = t.n


def describe(t: Tag) -> str:
    return "tag" + str(t.n)


def fail_nested() -> None:
    raise ValueError(describe(Tag(7)))  # the temp sits under `describe`, one level below the ctor


def fail_direct() -> None:
    raise TagError(Tag(3))  # the ctor's own argument binds directly: no temp, the ordinary form


def main() -> None:
    try:
        fail_nested()
    except ValueError as e:
        print("nested:", e)
    try:
        fail_direct()
    except TagError as e:
        print("direct:", e.n)


main()
