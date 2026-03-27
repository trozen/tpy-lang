# enumerate() builtin over different iterables

def main() -> None:
    # list of strings
    words = ["a", "b", "c"]
    for i, s in enumerate(words):
        print(i, s)

    # list of ints
    nums = [10, 20, 30]
    for i, n in enumerate(nums):
        print(i, n)

    # empty list
    empty: list[int] = []
    for i, n in enumerate(empty):
        print(i, n)

    # single element
    one = ["only"]
    for i, s in enumerate(one):
        print(i, s)

    # with start parameter
    letters = ["x", "y", "z"]
    for i, s in enumerate(letters, 10):
        print(i, s)

main()
