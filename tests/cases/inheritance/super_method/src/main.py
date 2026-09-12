from tpy import int32

class Animal:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "Animal says: ..."

    def describe(self) -> str:
        return self.name


class Dog(Animal):
    breed: str

    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)
        self.breed = breed

    def speak(self) -> str:
        return "Woof!"

    def full_speak(self) -> str:
        # Call overridden parent method via super()
        parent_speak = super().speak()
        return parent_speak


d = Dog("Rex", "Labrador")
print(d.speak())
print(d.full_speak())
print(d.describe())
