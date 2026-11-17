# Cycle peers still being imported whose bindings or `__all__` the
# interpreter cannot read as plain statements: a name a star import copies
# from a compound statement, a star import below an explicit import of the
# same name, and an `__all__` that is not a literal while the star runs.
import uc
import ua
import ub
import pa
import pb
import ka
import kb
import la
import lb


def main() -> None:
    print("uncertain:", ub.f())
    pb.main()
    kb.main()
    lb.main()


main()
