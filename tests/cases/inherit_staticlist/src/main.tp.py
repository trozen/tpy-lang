from tpy import StaticList, Int32


class IntStack(StaticList[Int32, 100]):
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def push(self, value: Int32) -> None:
        self.append(value)


def main() -> Int32:
    stack = IntStack("my_stack")

    # Use inherited append (via push wrapper)
    stack.push(Int32(10))
    stack.push(Int32(20))
    stack.push(Int32(30))

    # Use inherited __len__
    print(len(stack))

    # Use inherited __getitem__
    print(stack[0])
    print(stack[1])
    print(stack[2])

    # Use own field
    print(stack.name)

    return Int32(0)


main()
