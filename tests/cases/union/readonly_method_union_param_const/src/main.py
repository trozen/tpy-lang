# Sync inferred-readonly method, union param used discriminant-only (no member
# access -> address does not escape) -> the verdict deep-consts it, so the
# param is const everywhere. Complements readonly_method_union_param (member
# access -> address escapes -> mutable everywhere).
class Dog:
    def __init__(self) -> None:
        pass


class Cat:
    def __init__(self) -> None:
        pass


class Classifier:
    def __init__(self) -> None:
        pass

    def which(self, a: Dog | Cat) -> int:
        if isinstance(a, Dog):
            return 1
        return 2


def main() -> None:
    c = Classifier()
    d: Dog | Cat = Dog()
    print(c.which(d))
    t: Dog | Cat = Cat()
    print(c.which(t))


main()
