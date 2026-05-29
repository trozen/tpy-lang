# generic *args: iterate the pack directly and after a slice (element type is
# the type param T, resolved from the call site)
def first[T](*args: T) -> T:
    for x in args:  # tpyc: ok
        return x
    return args[0]

def count_rest[T](*args: T) -> int:
    n = 0
    for _ in args[1:]:  # tpyc: ok
        n += 1
    return n

def main() -> None:
    print(first(10, 20, 30))
    print(first("a", "b"))
    print(count_rest(1, 2, 3, 4))

main()
