# D16 phase 9: delattr with a runtime name requires __delattr__.


class Plain:
    field: str

    def __init__(self) -> None:
        self.field = "x"


def main() -> None:
    p = Plain()
    name = "field"
    delattr(p, name)  # tpyc: error(/has no field \(runtime name\) and does not define __delattr__/)


main()
