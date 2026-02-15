from tpy import Char


def eq_left(o: Char | None, c: Char) -> bool:
    return o == c  # tpyc: ok


def eq_right(c: Char, o: Char | None) -> bool:
    return c == o  # tpyc: ok


def ne_left(o: Char | None, c: Char) -> bool:
    return o != c  # tpyc: ok


def ne_right(c: Char, o: Char | None) -> bool:
    return c != o  # tpyc: ok


a: Char = "a"
b: Char = "b"
none_char: Char | None = None
some_a: Char | None = a

print(eq_left(some_a, a))
print(eq_left(some_a, b))
print(eq_left(none_char, a))

print(eq_right(a, some_a))
print(eq_right(a, none_char))

print(ne_left(none_char, a))
print(ne_right(a, none_char))
