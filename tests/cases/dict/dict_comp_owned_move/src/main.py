# Regression: a dict comprehension value needs an Own[T] move-temp (Box(i*10)
# moves the last-use loop var); the temp must land inside the loop body, not the
# enclosing statement scope where the loop var is undeclared. @nocopy Box makes
# a silent copy a compile error.
from tpy import Int32
from tplib.box import Box


def main(n: Int32) -> None:
    d = {i: Box(i * 10) for i in range(n)}
    print(len(d), d[0].get(), d[2].get())


main(3)
