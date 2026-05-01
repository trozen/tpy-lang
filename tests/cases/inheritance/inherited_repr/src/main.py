# Inherited __repr__ / __str__ on a user record drives operator<< for
# subclasses that don't define their own. Native ancestors (e.g.
# BaseException) are skipped so user Exception subclasses still get the
# field-by-field default printer.
class Animal:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"Animal({self.name})"

class Dog(Animal):
    def __init__(self, name: str) -> None:
        super().__init__(name)

class Cat(Animal):
    def __init__(self, name: str) -> None:
        super().__init__(name)

    def __repr__(self) -> str:
        return f"Cat({self.name})"

class Speaker:
    msg: str

    def __init__(self, msg: str) -> None:
        self.msg = msg

    def __str__(self) -> str:
        return f"<{self.msg}>"

class Echo(Speaker):
    def __init__(self, msg: str) -> None:
        super().__init__(msg)

class Pet(Dog):
    # Grandparent (Animal) defines __repr__; Dog has none; Pet has none.
    # Walks the full user MRO to find Animal.__repr__.
    def __init__(self, name: str) -> None:
        super().__init__(name)

class LoudCat(Cat):
    # Parent (Cat) overrides Animal.__repr__; closer-wins per MRO.
    def __init__(self, name: str) -> None:
        super().__init__(name)

def main() -> None:
    d = Dog("rex")
    print(d)
    print(repr(d))

    c = Cat("whiskers")
    print(c)
    print(repr(c))

    e = Echo("hello")
    print(e)

    p = Pet("rover")
    print(p)

    lc = LoudCat("garfield")
    print(lc)

main()
