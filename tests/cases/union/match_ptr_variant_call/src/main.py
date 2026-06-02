# Regression: match on a pointer-variant union returned from a METHOD call binds
# the subject by value (the variant is a prvalue, not X&) -- was ill-formed C++.
class Dog:
    name: str

    def __init__(self, n: str) -> None:
        self.name = n


class Cat:
    name: str

    def __init__(self, n: str) -> None:
        self.name = n


class Picker:
    def choose(self, d: Dog) -> Dog | Cat:
        return d


def main() -> None:
    p = Picker()
    d = Dog("rex")
    match p.choose(d):
        case Dog() as x:
            print(x.name)
        case Cat() as y:
            print(y.name)


main()
