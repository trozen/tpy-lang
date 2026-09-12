# Calling a union type alias as a constructor is not allowed
from tpy import int32

class Dog:
    age: int32
    def __init__(self, age: int32) -> None:
        self.age = age

class Cat:
    age: int32
    def __init__(self, age: int32) -> None:
        self.age = age

type Pet = Dog | Cat

x = Pet()  # tpyc: error(/not callable/)
