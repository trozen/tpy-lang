# Defining a method that hides a parent method emits a warning at the child
# class definition. Static dispatch means parent-typed references will call
# the parent's method, not the child's -- differs from Python.
class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def speak(self) -> str:
        return "..."

class Dog(Animal):  # tpyc: warning(/hides 'Animal.speak'/)
    breed: str
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed
    def speak(self) -> str:
        return "Woof!"

def describe(a: Animal) -> str:
    return a.speak()

def main() -> None:
    d: Dog = Dog("Rex", "Lab")
    # ARG context intentionally does not emit a per-call-site warning;
    # the def-site warning on line 11 already flagged the hiding. Static
    # dispatch: Animal.speak is called, not Dog.speak (divergence from CPython).
    print(describe(d))

main()
