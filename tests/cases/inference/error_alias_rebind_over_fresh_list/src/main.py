# A list local holding a list of its own cannot be rebound to alias another
# list -- a documented limitation, BUGS.md#alias-rebind-over-fresh-list-rejected.
from tpy import int32


def rebind(xs: list[int32]) -> None:
    ys = [9]
    # CPython makes `ys` a second name for `xs`; the local's one storage cannot
    ys = xs  # tpyc: error(/it holds a list\[int32\] of its own and cannot be rebound to share an existing list\[int32\]/)
    ys.append(5)
    print(xs)


rebind([1])
