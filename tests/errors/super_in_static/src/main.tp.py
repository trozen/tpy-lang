# Error: super() in static method

class Parent:
    def method(self) -> None:
        pass


class Child(Parent):
    @staticmethod
    def helper() -> None:
        super().method()  # tpyc: error(/super\(\) cannot be used in a static method/)
