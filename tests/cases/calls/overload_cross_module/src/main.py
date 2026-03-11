# Cross-module @overload: import and call overloaded function from another module
from animals import Dog, Cat, describe

def main() -> None:
    d = Dog("Rex")
    c = Cat(9)
    print(describe(d))
    print(describe(c))

main()
