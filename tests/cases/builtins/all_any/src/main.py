# all() and any() builtins with lists and generator expressions

def main() -> None:
    t = [True, True, True]
    f1 = [True, False, True]
    f2 = [False, False, False]
    print(all(t))
    print(all(f1))
    print(all(f2))

    a1 = [True, False, False]
    a2 = [False, False, False]
    a3 = [False, False, True]
    print(any(a1))
    print(any(a2))
    print(any(a3))

    # generator expressions
    nums = [1, 2, 3, 4, 5]
    print(all(x > 0 for x in nums))
    print(all(x > 3 for x in nums))
    print(any(x > 4 for x in nums))
    print(any(x > 10 for x in nums))

    # empty iterables
    empty: list[bool] = []
    print(all(empty))
    print(any(empty))

    # non-bool: integers (truthy = nonzero)
    ints = [1, 2, 3]
    print(all(ints))
    ints_with_zero = [1, 0, 2]
    print(all(ints_with_zero))
    print(any(ints_with_zero))

    # strings (truthy = non-empty)
    strs = ["a", "b", "c"]
    print(all(strs))
    strs2 = ["a", "", "c"]
    print(all(strs2))
    print(any(strs2))

    # floats (truthy = nonzero)
    floats = [1.0, 2.0, 3.0]
    print(all(floats))
    floats2 = [1.0, 0.0, 3.0]
    print(all(floats2))

main()
