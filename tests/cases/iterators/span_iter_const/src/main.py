# Test that __iter__() -> SpanIter[T] with @auto_readonly allows iteration from
# both mutable and const contexts. Mutable __iter__ yields SpanIter<T>, const yields SpanIter<const T>.
from tpy import int32, Span, SpanIter, readonly, auto_readonly

class Stack:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = []

    def push(self, val: int32) -> None:
        self._data.append(val)

    @auto_readonly
    def __span__(self) -> Span[auto_readonly[int32]]:
        return self._data

    @auto_readonly
    def __iter__(self) -> SpanIter[auto_readonly[int32]]:
        return SpanIter(self.__span__())

    @readonly
    def sum(self) -> int32:
        total: int32 = 0
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
