# A module global read at its last use by an Own[T] parameter keeps its
# value: other bodies read the global later, so the sink takes a warned copy.
from tpy import Own


def take(xs: Own[list[str]]) -> Own[list[str]]:
    return xs


def show(tag: str) -> None:
    print(tag, g)


def in_function() -> None:
    # function: the global's last use in this body is the Own[T] argument
    n = len(take(g))  # tpyc: warning(/copies list\[str\] into owned storage/)
    print("function: took", n)


g = ["a", "b", "c"]
# __tpy_init: the global's last use among the module's statements
print("module: took", len(take(g)))  # tpyc: warning(/copies list\[str\] into owned storage/)
show("module: after")
in_function()
show("function: after")
