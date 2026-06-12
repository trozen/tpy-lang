# __all__ += [...] extends the star-import export set (literal
# concatenation at parse time; __all__ itself emits no runtime code).
from explib import *


def main() -> None:
    one()
    two()


main()
