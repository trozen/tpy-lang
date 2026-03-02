# Error: cannot infer method type argument when no args provide type info

class Converter:
    def __init__(self):
        pass

    def convert[U](self, val: int) -> int:
        return val

def main() -> None:
    c = Converter()
    c.convert(42)  # tpyc: error(/Cannot infer type arguments/)

main()
