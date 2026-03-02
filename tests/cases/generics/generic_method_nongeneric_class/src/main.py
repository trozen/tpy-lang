# Method-level type parameter on a non-generic class
class Converter:
    def __init__(self):
        pass

    def identity[U](self, val: U) -> U:
        return val

def main() -> None:
    c = Converter()
    print(c.identity(42))
    print(c.identity("hello"))
    print(c.identity(True))

main()
