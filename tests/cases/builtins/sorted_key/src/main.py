# sorted() with key= parameter

def negate(x: int) -> int:
    return -x

def main() -> None:
    # sort by negation (reverse order)
    a = [3, 1, 4, 1, 5]
    print(sorted(a, key=negate))

    # sort by absolute value using lambda
    b = [-3, 1, -4, 1, 5, -9]
    print(sorted(b, key=lambda x: x if x >= 0 else -x))

    # sort strings by length
    words = ["banana", "pie", "apple", "kiwi"]
    print(sorted(words, key=lambda s: len(s)))

    # stability: equal-key elements preserve original order
    pairs = ["bb", "aa", "cc", "ab", "ba"]
    print(sorted(pairs, key=lambda s: len(s)))

main()
