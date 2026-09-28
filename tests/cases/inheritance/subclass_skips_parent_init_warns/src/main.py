# A subclass __init__ that skips an initializer its base is owed compiles with
# a warning naming the divergence (LANGUAGE_FEATURES "Single class inheritance").
from dataclasses import dataclass

from tpy import int32, error_return, ReturnException


class Labeled:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label


class Animal:
    kind: str

    def __init__(self) -> None:
        self.kind = "animal"


class Pet(Animal):
    pass


# direct TPy parent: TPy value-initializes `Labeled` (`label` empty), where CPython leaves it unset
class Tag(Labeled):
    n: int32

    def __init__(self, n: int32) -> None:  # tpyc: warning(/'Tag.__init__' does not call 'super\(\).__init__\(...\)'; TPy default-constructs the 'Labeled' part instead$/)
        self.n = n


# grandparent-inherited __init__: default-constructing Pet runs Animal.__init__()
class Dog(Pet):
    n: int32

    def __init__(self, n: int32) -> None:  # tpyc: warning(/'Dog.__init__' does not call 'super\(\).__init__\(...\)'; TPy default-constructs the 'Pet' part instead, which runs 'Animal.__init__\(\)'$/)
        self.n = n


class AppError(Exception):
    pass


# `@native` Exception parent reached through an `__init__`-less class
class CodeError(AppError):
    code: int32

    def __init__(self, code: int32) -> None:  # tpyc: warning(/'CodeError.__init__' does not call 'super\(\).__init__\(...\)'; TPy default-constructs the 'AppError' part instead$/)
        self.code = code


# return exception: the skipped call is what sets the message
class Failed(Exception, ReturnException):
    code: int32

    def __init__(self, code: int32) -> None:  # tpyc: warning(/'Failed.__init__' does not call 'super\(\).__init__\(...\)'; TPy default-constructs the 'Exception' part instead$/)
        self.code = code


@error_return(Failed)
def run(ok: bool) -> int32:
    if ok:
        return 1
    raise Failed(7)


# multi-base: one warning per skipped base
class Both(Animal, Labeled):
    n: int32

    def __init__(self, n: int32) -> None:  # tpyc: warning(/'Both.__init__' does not call 'Animal.__init__\(self, ...\)'/) warning(/'Both.__init__' does not call 'Labeled.__init__\(self, ...\)'/)
        self.n = n


# @dataclass child: its synthesized __init__ calls no parent initializer, as in CPython
@dataclass
class Kid(Animal):  # tpyc: warning(/the '__init__' synthesized by '@dataclass' for 'Kid' does not call 'Animal.__init__'; TPy default-constructs the 'Animal' part instead, which runs 'Animal.__init__\(\)'; write an explicit '__init__' that calls it/)
    z: int32


# builtin container base: CPython's `__new__` already built the empty list, so no warning
class Bag(list[int32]):
    n: int32

    def __init__(self, n: int32) -> None:  # tpyc: ok
        self.n = n


def main() -> None:
    print("tag:", Tag(1).n)
    print("dog:", Dog(2).n)
    try:
        raise CodeError(3)
    except CodeError as e:
        print("native exception:", e.code)
    try:
        run(False)
    except Failed as e:
        print("return exception:", e.code)
    print("multi-base:", Both(4).n)
    print("dataclass:", Kid(5).z)
    b = Bag(6)
    b.append(1)
    print("list base:", b.n, len(b))


main()
