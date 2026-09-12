# Non-readonly call invalidates subscript narrowing even when the container
# isn't passed as an argument (mutation through global alias).
from tpy import int32, Own, copy

def make_list() -> Own[list[int32 | None]]:
    result: list[int32 | None] = []
    result.append(int32(1))
    return copy(result)  # tpyc: warning(/unnecessary copy/)

l: list[int32 | None] = make_list()

# Mutates global l, which may alias the parameter
def mut() -> None:
    l[0] = None

def f(items: list[int32 | None]) -> None:
    if items[0] is not None:
        mut()
        i: int32 = int32(1) + items[0]  # tpyc: warning(/Potential None access/)
        print(i)

f(l)
