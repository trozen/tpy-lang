# Regression guard for the native_field rename through subclasses: a TPy
# subclass redeclaring a field name that its @native ancestor renames
# (OSError.strerror -> strerror_text) must bind its OWN plain member on
# access, not the ancestor's renamed one, while other inherited renamed
# fields (.errno) keep resolving. Reads through the subclass type only --
# a base-typed read of a shadowed field is static in TPy (declared
# shadowing semantics, warned below) and would diverge from CPython.
from tpy import String


class Weird(OSError):
    strerror: str  # tpyc: warning(/shadows inherited field from 'OSError'/)

    def __init__(self, message: String = "") -> None:
        super().__init__(message)
        self.strerror = "own-field"


def main() -> None:
    w = Weird("boom")
    print(w.strerror)
    w.errno = 3
    print(w.errno)


main()
