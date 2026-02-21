# Test implicit truthiness via __bool__() in if/while/not/and/or

class Container:
    count: int

    def __init__(self, count: int) -> None:
        self.count = count

    def __bool__(self) -> bool:
        return self.count != 0

def main() -> None:
    c1 = Container(3)
    c2 = Container(0)

    # if with __bool__
    if c1:
        print("c1 truthy")
    if c2:
        print("c2 truthy")

    # while with __bool__ (reassigned variable uses pointer slot)
    c3 = Container(2)
    while c3:
        print(c3.count)
        c3 = Container(c3.count - 1)

    # not with __bool__
    if not c2:
        print("c2 falsy")

    # and/or with __bool__
    if c1 and not c2:
        print("c1 and not c2")

main()
