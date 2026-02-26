# Error: covariant containers (list[Dog] -> list[Animal]) are not allowed
class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Dog(Animal):
    def __init__(self, name: str) -> None:
        super().__init__(name)

def main() -> None:
    dogs: list[Dog] = [Dog("Buddy")]
    animals: list[Animal] = dogs  # tpyc: error(/Type mismatch/)

main()
