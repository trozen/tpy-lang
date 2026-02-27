# Auto-declare string fields from __init__
class Person:
    def __init__(self, name: str, city: str):
        self.name = name
        self.city = city


def main() -> None:
    p = Person("Alice", "NYC")
    print(p.name)
    print(p.city)


main()
