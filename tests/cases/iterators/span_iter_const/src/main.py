# Test that __iter__() -> SpanIter[T] generates dual overloads (const + non-const),
# allowing iteration from both mutable and const contexts (e.g. __repr__).
from tpy import Int32, Span, SpanIter, readonly

class Stack:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = []

    def push(self, val: Int32) -> None:
        self._data.append(val)

    def __span__(self) -> Span[Int32]:
        return self._data

    def __iter__(self) -> SpanIter[Int32]:
        return SpanIter(self.__span__())

    @readonly
    def sum(self) -> Int32:
        total: Int32 = 0
        for x in self:
            total += x
        return total

    def __repr__(self) -> str:
        s = "Stack("
        first = True
        for x in self:
            if not first:
                s += ", "
            first = False
            s += str(x)
        s += ")"
        return s

def main() -> None:
    s = Stack()
    s.push(10)
    s.push(20)
    s.push(30)

    # Non-const iteration
    for x in s:
        print(x)

    # Const iteration via __repr__
    print(repr(s))

    # Const iteration via readonly method
    print(s.sum())

main()
