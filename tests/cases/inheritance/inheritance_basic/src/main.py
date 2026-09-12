from tpy import int32

class Animal:
    name: str
    age: int32

    def __init__(self, name: str, age: int32) -> None:
        self.name = name
        self.age = age

    def speak(self) -> str:
        return "..."

    def describe(self) -> str:
        return self.name


class Dog(Animal):
    breed: str

    def __init__(self, name: str, age: int32, breed: str) -> None:
        self.name = name
        self.age = age
        self.breed = breed

    def speak(self) -> str:
        return "Woof!"


# Test creating a Dog (child class)
d = Dog("Buddy", 3, "Golden Retriever")

# Access child field
print(d.breed)

# Access inherited fields
print(d.name)
print(d.age)

# Call overridden method
print(d.speak())

# Call inherited method
print(d.describe())

# Test creating an Animal (parent class)
a = Animal("Generic", 5)
print(a.speak())
print(a.describe())
