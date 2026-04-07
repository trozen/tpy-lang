# del on non-value type locals (list, str)
def main() -> None:
    items: list[int] = [1, 2, 3]
    print(len(items))
    del items
    items = [4, 5]
    print(len(items))

    s = "hello"
    print(s)
    del s
    s = "world"
    print(s)

main()
