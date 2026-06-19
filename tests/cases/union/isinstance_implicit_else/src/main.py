# isinstance narrowing where the remaining member is reached via implicit
# fall-through (no explicit `else:`) -- the narrowed member access must resolve.
class Cat:
    def __init__(self, a: int):
        self.a = a
class Dog:
    def __init__(self, b: int):
        self.b = b

def show(x: Cat | Dog) -> int:
    if isinstance(x, Cat):
        return x.a
    return x.b          # x is Dog here, reached by falling through the return

def main() -> None:
    print(show(Cat(1)))
    print(show(Dog(2)))

main()
