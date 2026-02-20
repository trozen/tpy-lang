# Test: unnecessary copy() warning for static method and protocol method Own[T] params
from typing import Protocol
from tpy import Int32, Own, copy

class Point:
    x: Int32
    y: Int32

class Holder(Protocol):
    def store(self, p: Own[Point]) -> None: ...

class MyHolder:
    def store(self, p: Own[Point]) -> None:
        print(p.x)

class Factory:
    @staticmethod
    def consume(p: Own[Point]) -> Int32:
        return p.x

def test_static() -> None:
    p: Point = Point()
    p.x = 10
    # copy() at last use -- should warn unnecessary
    result: Int32 = Factory.consume(copy(p))  # tpyc: warning(/unnecessary copy/)
    print(result)

def test_protocol() -> None:
    h: MyHolder = MyHolder()
    p: Point = Point()
    p.x = 20
    # copy() at last use -- should warn unnecessary
    h.store(copy(p))  # tpyc: warning(/unnecessary copy/)

def main() -> None:
    test_static()
    test_protocol()

main()
