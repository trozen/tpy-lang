from tpy import Int32

class Stack[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = list[T]()

    def push(self, value: T) -> None:
        self.items.append(value)

    def pop(self) -> T:
        return self.items.pop()

    def is_empty(self) -> bool:
        return len(self.items) == 0


def main() -> None:
    stack: Stack[Int32] = Stack[Int32]()
    print(stack.is_empty())  # True

    stack.push(10)
    stack.push(20)
    stack.push(30)
    print(stack.is_empty())  # False

    print(stack.pop())  # 30
    print(stack.pop())  # 20
    print(stack.pop())  # 10
    print(stack.is_empty())  # True


main()
