# Calling a union type alias as a constructor is not allowed
from tpy import Int32

class Dog:
    age: Int32
    def __init__(self, age: Int32) -> None:
        self.age = age

class Cat:
    age: Int32
    def __init__(self, age: Int32) -> None:
        self.age = age

type Pet = Dog | Cat

x = Pet()  # tpyc: error(/not callable/)
