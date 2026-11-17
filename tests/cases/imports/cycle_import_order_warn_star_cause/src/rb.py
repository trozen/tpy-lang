# The warning names the late import's line and says to move that import up.
from ra import *  # tpyc: warning(/'ra' is still being imported -- .* before it binds 'E' \(ra\.py:3\); CPython's star import does not bind it here, so a use of it raises NameError\. Move the import at ra\.py:3 above that import, or import 'rb' before 'ra' in main\.py\./)


def main() -> None:
    print("late import:", E().v)
