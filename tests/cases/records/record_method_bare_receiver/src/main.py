# User-record method calls on bare-name receivers (record args, void stmt
# calls, inherited/narrowed); mutations observed on the caller's object (aliasing).
from tpy import Int32


class Counter:
    value: Int32

    def __init__(self, start: Int32):
        self.value = start

    def bump(self) -> None:
        self.value += 1

    def add_from(self, other: "Counter") -> None:
        self.value += other.value

    def diff(self, other: "Counter") -> Int32:
        return self.value - other.value

    def get(self) -> Int32:
        return self.value


class Label:
    name: str

    def __init__(self, name: str):
        self.name = name


class FancyCounter(Counter):
    def __init__(self, start: Int32):
        super().__init__(start)


def combine(a: Counter, b: Counter) -> Int32:
    a.bump()          # void, statement position
    a.add_from(b)     # record name as method arg
    return a.diff(b)


def narrowed_receiver(v: Counter | Label) -> Int32:
    if isinstance(v, Counter):
        v.bump()
        return v.get()
    return len(v.name)


def inherited(f: FancyCounter, k: Int32) -> Int32:
    f.bump()
    return f.get() + k


def main():
    a = Counter(3)
    b = Counter(4)
    print(combine(a, b))
    print(a.get())    # 8: mutated through the receiver param
    print(b.get())    # 4: the arg was only read
    c = Counter(10)
    print(narrowed_receiver(c))
    print(c.get())    # 11: mutated through the narrowed alias
    print(narrowed_receiver(Label("abc")))
    f = FancyCounter(20)
    print(inherited(f, 2))
    print(f.get())    # 21


main()
