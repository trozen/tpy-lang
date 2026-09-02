from tpy import Int32, Own
def two[T](a: T) -> Own[list[T]]:
    return [a, a]
def one[T](a: Own[T]) -> Own[list[T]]:
    return [a]
def main() -> None:
    print(len(two(1)), len(one(2)))
main()
