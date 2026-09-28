from tpy import int32
from typing import Protocol

# Base class
class Vehicle:
    brand: str
    year: int32

    def __init__(self, brand: str, year: int32) -> None:
        self.brand = brand
        self.year = year

    def get_brand(self) -> str:
        return self.brand


# Protocols
class Printable(Protocol):
    def __str__(self) -> str:
        ...


class Measurable(Protocol):
    def weight(self) -> int32:
        ...


class Describable(Protocol):
    def describe(self) -> str:
        ...


# Inherit from class AND implement multiple protocols
class Car(Vehicle, Printable, Measurable, Describable):
    model: str
    car_weight: int32

    def __init__(self, brand: str, year: int32, model: str, car_weight: int32) -> None:
        super().__init__(brand, year)
        self.model = model
        self.car_weight = car_weight

    def __str__(self) -> str:
        return self.model

    def weight(self) -> int32:
        return self.car_weight

    def describe(self) -> str:
        return "A car"


# Test combined inheritance with multiple protocols
c = Car("Toyota", 2023, "Camry", 1500)

# Access inherited fields
print(c.brand)
print(c.year)

# Access own fields
print(c.model)
print(c.car_weight)

# Call inherited method
print(c.get_brand())

# Call protocol methods
print(c.__str__())
print(c.weight())
print(c.describe())
