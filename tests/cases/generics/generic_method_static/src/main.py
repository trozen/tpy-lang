# Static method with its own type parameter
from tpy import Int32

class Utils:
    def __init__(self):
        pass

    @staticmethod
    def identity[U](val: U) -> U:
        return val

def main() -> None:
    # Explicit type arg on non-generic class
    print(Utils.identity[Int32](Int32(42)))
    print(Utils.identity[bool](True))

main()
