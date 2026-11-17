# `eb` took E from `other` above its star import of the suspended `ea`:
# CPython keeps other's E, while TPy's star import binds ea's over it.
from other import E
from ea import *  # tpyc: warning(/'ea' is still being imported -- .* before it defines 'E'; CPython's star import does not bind it here, so a use of it reads the binding it had before that import\. Define 'E' above that import in ea.py, or import 'eb' before 'ea' in main.py\./)


def main() -> None:
    print("earlier import:", E().v)
