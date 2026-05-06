# D16 phase 9: setattr with a runtime name requires __setattr__.


class Plain:
    field: str

    def __init__(self) -> None:
        self.field = "x"


def main() -> None:
    p = Plain()
    name = "field"
    setattr(p, name, "v")  # tpyc: error(/has no field \(runtime name\) and does not define __setattr__/)


main()
