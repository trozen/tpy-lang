# Forwarding a slice of a *args body view into another *args parameter works:
# the slice stays a varargs, so *items[1:] unpacks into g's vararg slot.
def g(*nums: int) -> int:
    t = 0
    for n in nums:
        t += n
    return t


def f(*items: int) -> int:
    return g(*items[1:])


def main() -> None:
    print(f(10, 1, 2, 3))


main()
