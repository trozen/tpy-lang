# Test bool() dispatch to __bool__() dunder method on user-defined classes

class Container:
    count: int

    def __init__(self, count: int) -> None:
        self.count = count

    def __bool__(self) -> bool:
        return self.count != 0

def main() -> None:
    # Non-empty container -> True
    c1 = Container(3)
    print(bool(c1))  # True

    # Empty container -> False
    c2 = Container(0)
    print(bool(c2))  # False

    # Direct __bool__() call
    print(c1.__bool__())  # True
    print(c2.__bool__())  # False

main()
