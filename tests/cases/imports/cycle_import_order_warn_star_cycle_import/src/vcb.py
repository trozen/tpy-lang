# The late binding is the suspending import: it cannot move above itself.
from vca import *  # tpyc: warning(/'vca' is still being imported -- .* before it binds 'E' \(vca\.py:2\); CPython's star import does not bind it here, so a use of it raises NameError\. Move the shared definitions into a module outside the cycle\.$/)


def main() -> None:
    print("via cycle:", E().v)
