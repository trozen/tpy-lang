# A list-of-lists local rebound to [[], [2]]: only the local's type makes the
# empty row float, so the rebind refuses the int row and names the local.


def rebind(xss: list[list[float]]) -> None:
    zs = xss
    zs = [[], [2]]  # tpyc: error(/'zs' is bound to float elements at line 6 and to int elements here.*float\(\.\.\.\) on the int value/)
    print(zs)


rebind([[1.5]])
