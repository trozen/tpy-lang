# Regression: union param on a readonly method, narrowed member access (address
# escapes -> mutable everywhere). Before the fix the call site forced deep-const
# off a coarse readonly flag and clashed with the mutable signature.
class Dog:
    def __init__(self) -> None:
        pass

    def sound(self) -> str:
        return "woof"


class Cat:
    def __init__(self) -> None:
        pass

    def sound(self) -> str:
        return "meow"


class Speaker:
    def __init__(self) -> None:
        pass

    def voice(self, a: Dog | Cat) -> str:
        if isinstance(a, Dog):
            return a.sound()
        return "?"


def main() -> None:
    s = Speaker()
    d: Dog | Cat = Dog()
    print(s.voice(d))
    c: Dog | Cat = Cat()
    print(s.voice(c))


main()
