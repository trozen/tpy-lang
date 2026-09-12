from tpy import char


def eq_left(o: char | None, c: char) -> bool:
    return o == c  # tpyc: ok


def eq_right(c: char, o: char | None) -> bool:
    return c == o  # tpyc: ok


def ne_left(o: char | None, c: char) -> bool:
    return o != c  # tpyc: ok


def ne_right(c: char, o: char | None) -> bool:
    return c != o  # tpyc: ok


a: char = "a"
b: char = "b"
none_char: char | None = None
some_a: char | None = a

print(eq_left(some_a, a))
print(eq_left(some_a, b))
print(eq_left(none_char, a))

print(eq_right(a, some_a))
print(eq_right(a, none_char))

print(ne_left(none_char, a))
print(ne_right(a, none_char))
