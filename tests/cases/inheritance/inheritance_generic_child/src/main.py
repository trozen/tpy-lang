from tpy import int32

# Case 1: Generic class inheriting from non-generic class
class Animal:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Box[T](Animal):
    value: T
    def __init__(self, name: str, value: T) -> None:
        self.name = name
        self.value = value
    def get(self) -> T:
        return self.value

# Case 2: Generic class inheriting from concrete generic parent
class Container[T]:
    value: T
    def __init__(self, value: T) -> None:
        self.value = value
    def get(self) -> T:
        return self.value

class Wrapper[U](Container[int32]):
    extra: U
    def __init__(self, value: int32, extra: U) -> None:
        self.value = value
        self.extra = extra

# Test Case 1: Generic child of non-generic parent
b = Box[int32]("mybox", 42)
print(b.name)   # inherited from Animal
print(b.get())  # own method returning T=int32

# Test Case 2: Generic child of concrete generic parent
w = Wrapper[str](100, "hello")
print(w.get())  # inherited, returns int32 (not U)
print(w.extra)  # own field of type U=str
