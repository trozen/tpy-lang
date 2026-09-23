# `Cls.m(self)` inside Cls itself is rejected with a message that names the
# class (a subclass override would make `self.m()` a different call).
class Shape:
    def name(self) -> str:
        return "shape"

    def describe(self) -> str:
        return Shape.name(self)  # tpyc: error(/'Shape.name\(self, ...\)' calls a method of 'Shape' itself, which is not supported/)


def main() -> None:
    print(Shape().describe())


main()
