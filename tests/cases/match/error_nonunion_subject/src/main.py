# error: match subject must be a union, enum, or primitive type
class Foo:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

def describe(f: Foo) -> str:
    match f:  # tpyc: error(/must be a union, enum, or primitive type/)
        case _:
            return "something"
    return ""

def main() -> None:
    pass

main()
