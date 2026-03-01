# Keyword arguments with generic functions

def first[T](a: T, b: T) -> T:
    return a

def main() -> None:
    print(first(a=1, b=2))
    print(first(10, b=20))
    print(first(b="world", a="hello"))

main()
