# A tuple local with a float-or-None element rebound to a tuple with an int
# there is refused: the element's None does not hide the int/float mix.


def rebind(c: bool) -> None:
    p = (0.5 if c else None, "a")
    p = (3, "b")  # tpyc: error(/'p' is bound to float elements at line 6 and to int elements here.*write 3\.0 instead of 3/)
    print(p)


rebind(True)
