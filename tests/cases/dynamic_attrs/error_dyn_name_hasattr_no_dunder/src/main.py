# D16 phase 9: hasattr with a runtime name requires __getattr__ (no
# compile-time fold to False since we can't know what the runtime name is).


class Plain:
    field: str

    def __init__(self) -> None:
        self.field = "x"


def main() -> None:
    p = Plain()
    name = "field"
    print(hasattr(p, name))  # tpyc: error(/has no field \(runtime name\) and does not define __getattr__/)


main()
