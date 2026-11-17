# Star imports in import cycles CPython imports: every name the source
# exports is bound by the time the star import runs -- no warning.
import sa
import ka
import ua
from va import *  # noqa: F403


def main() -> None:
    sa.main()
    ka.main()
    print("own names:", ua.helper(), ua.late())
    print("late __all__:", A(), B())


main()
