# A tuple param with a union element (tuple[Dog | Cat, int32]) builds and is
# passed by borrow -- @nocopy members prove the element is aliased, not copied.
from tpy import int32, nocopy


@nocopy
class Dog:
    bark: int32

    def __init__(self, b: int32) -> None:
        self.bark = b


@nocopy
class Cat:
    meow: int32

    def __init__(self, m: int32) -> None:
        self.meow = m


def read_second(pair: tuple[Dog | Cat, int32]) -> int32:
    return pair[1]


def passthrough(pair: tuple[Dog | Cat, int32]) -> int32:
    return read_second(pair)


def main() -> None:
    print("dog:", read_second((Dog(7), 2)))
    print("cat:", read_second((Cat(9), 3)))
    print("pass:", passthrough((Dog(1), 5)))


main()
