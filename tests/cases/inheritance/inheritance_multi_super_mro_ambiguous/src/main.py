# Both direct parents define greet(); MRO-aware super() picks the MRO-first
# one (Speaker), not an ambiguity error.


class Speaker:
    def greet(self) -> str:
        return "Speaker.greet"


class Greeter:
    def greet(self) -> str:
        return "Greeter.greet"


class Child(Speaker, Greeter):
    def greet(self) -> str:
        return super().greet() + " / child"


def main() -> None:
    c = Child()
    print(c.greet())


main()
