# D16 phase 9: dynamic-name builtin requires the class to define the
# matching dunder. With a runtime name we can't fold to compile-time
# True/False (hasattr) or to declared-member access (getattr).


class Plain:
    field: str

    def __init__(self) -> None:
        self.field = "x"


def main() -> None:
    p = Plain()
    name = "field"
    print(getattr(p, name, "fallback"))  # tpyc: error(/has no field \(runtime name\) and does not define __getattr__/)


main()
