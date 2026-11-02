# A nested def reading a name the enclosing function binds only in a `while`
# body, after the loop: the line named is the binding loop's header, found
# from the body binding (the `for` case finds it from the loop target).
from tpy import int32


def while_body() -> None:
    n = 0
    while n < 3:
        x = n
        n += 1

    def inner() -> int32:
        # The read of the body-bound name from the nested def.
        return x  # tpyc: error(/'x' is read by nested function 'inner' after the loop that binds it on line 9; TPy does not yet support reading a name bound in a loop from a nested function defined after that loop/)
    print(inner())


while_body()
