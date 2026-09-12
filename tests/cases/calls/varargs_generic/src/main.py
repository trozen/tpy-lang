# generic *args: type parameter T inferred from call-site args
from tpy import int32

def first[T](*args: T) -> T:
    return args[0]

def count[T](*args: T) -> int32:
    return len(args)

def main() -> None:
    print(first(10, 20, 30))
    print(first("hello", "world"))
    print(count(1, 2, 3, 4))
    print(count("a"))

main()
