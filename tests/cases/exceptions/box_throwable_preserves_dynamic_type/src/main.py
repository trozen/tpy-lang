# Phase 20 headline invariant: a BaseException caught polymorphically,
# cloned into Box[Throwable], and re-raised preserves its dynamic type.
# This is the BUGS.md "BaseException slicing" entry's reproducer; the bug
# made a stored ValueError surface as BaseException at re-raise time so
# user-level `except ValueError` would silently miss.
#
# Mechanism under test:
#   except BaseException as e   -> e is a borrow of the dynamic type
#   Box(e.clone())              -> clone() virtual-dispatches to the
#                                  concrete subclass's macro override,
#                                  returns unique_ptr<Throwable> holding
#                                  a polymorphic copy at the actual type
#   raise stored                -> Stage 3 desugar -> stored.__raise__();
#                                  Box auto-derefs through Throwable's
#                                  vtable; __raise__'s `throw *this` runs
#                                  on the concrete type
from tplib import Box
from tpy import Throwable


def main() -> None:
    try:
        raise ValueError("boom")
    except BaseException as e:
        stored: Box[Throwable] = Box(e.clone())
        try:
            raise stored
        except ValueError as v:
            print("caught as ValueError:", v.message)
        except BaseException:
            print("caught as BaseException -- slicing bug regressed!")


main()
