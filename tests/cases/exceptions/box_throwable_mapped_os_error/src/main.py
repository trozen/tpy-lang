# A mapped errno-form OSError survives clone() -> Box[Throwable] -> re-raise:
# clone's implicit copy carries mapped_kind, so the stored value's __raise__
# still dispatches the PEP 3151 subclass. Covers both shapes: cloning a
# CAUGHT mapped exception (dynamic type already FileNotFoundError, the macro
# override rethrows it) and cloning an UN-raised plain-OSError value (static
# and dynamic type OSError, the dispatching override maps at re-raise).
from tplib import Box
from tpy import Throwable


def main() -> None:
    try:
        raise OSError(2, "No such file or directory")
    except OSError as e:
        stored: Box[Throwable] = Box(e.clone())
        try:
            raise stored
        except FileNotFoundError as f:
            print("caught clone as FileNotFoundError:", f.errno)
        except OSError:
            print("WRONG: mapping lost through clone")
    unraised = OSError(17, "File exists")
    boxed: Box[Throwable] = Box(unraised.clone())
    try:
        raise boxed
    except FileExistsError as f:
        print("unraised clone maps at raise:", f.errno)
    except OSError:
        print("WRONG: unraised clone did not map")


main()
