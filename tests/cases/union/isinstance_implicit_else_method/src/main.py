# Method call (not just field access) through an implicit-else union narrowing:
# dispatch on the narrowed member must resolve to that member's method.
class Cat:
    def __init__(self, a: int):
        self.a = a
    def speak(self) -> int:
        return self.a + 10
class Dog:
    def __init__(self, b: int):
        self.b = b
    def speak(self) -> int:
        return self.b + 20

def show(x: Cat | Dog) -> int:
    if isinstance(x, Cat):
        return x.speak()
    return x.speak()        # x is Dog here; resolves Dog.speak via the narrowed alias

def main() -> None:
    print(show(Cat(1)))
    print(show(Dog(2)))

main()
