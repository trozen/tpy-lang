# Non-readonly call invalidates subscript narrowing even when the container
# isn't passed as an argument (mutation through global alias).
from tpy import Int32, Own, copy

def make_list() -> Own[list[Int32 | None]]:
    result: list[Int32 | None] = []
    result.append(Int32(1))
    return copy(result)  # tpyc: warning(/unnecessary copy/)

l: list[Int32 | None] = make_list()

# Mutates global l, which may alias the parameter
def mut() -> None:
    l[0] = None

def f(items: list[Int32 | None]) -> None:
    if items[0] is not None:
        mut()
        i: Int32 = Int32(1) + items[0]  # tpyc: warning(/Potential None access/)
        print(i)

f(l)
