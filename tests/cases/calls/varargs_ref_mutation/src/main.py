# *args with non-value types: mutations through args visible to caller
class Counter:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

def increment_all(*args: Counter) -> None:
    for c in args:
        c.value += 1

def set_values(*args: Counter) -> None:
    for i in range(len(args)):
        args[i].value = (i + 1) * 10

def main() -> None:
    a = Counter(0)
    b = Counter(0)
    c = Counter(0)

    # mutations through *args should be visible
    increment_all(a, b, c)
    print(a.value)
    print(b.value)
    print(c.value)

    # indexing access should also give references
    set_values(a, b)
    print(a.value)
    print(b.value)

    # c should still be 1 from earlier increment
    print(c.value)

main()
