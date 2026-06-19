# os.environ mapping (set/get/in/.get/del) + the os.putenv footgun. Each var is
# set then observed through a different surface, so a silent miscompile of the
# shared mapping would show. Byte-compared against CPython; uses only vars this
# program sets, so it is machine-independent.
import os


def main():
    os.environ["TPY_ENV_A"] = "alpha"
    print(os.environ["TPY_ENV_A"])              # write visible via []
    print("TPY_ENV_A" in os.environ)            # True
    print("TPY_ENV_MISSING" in os.environ)      # False
    print(os.getenv("TPY_ENV_A"))               # getenv reads the same mapping
    print(os.environ.get("TPY_ENV_A"))          # alpha
    print(os.environ.get("TPY_ENV_MISSING"))    # None
    print(os.environ.get("TPY_ENV_MISSING", "d"))  # d
    print(os.path.expandvars("$TPY_ENV_A/x"))   # alpha/x  (expandvars reads it)

    # Overwrite is visible.
    os.environ["TPY_ENV_A"] = "beta"
    print(os.environ["TPY_ENV_A"])              # beta

    # del removes it from the mapping.
    del os.environ["TPY_ENV_A"]
    print("TPY_ENV_A" in os.environ)            # False
    print(os.getenv("TPY_ENV_A", "gone"))       # gone

    # putenv footgun: writes libc only, never the os.environ snapshot, so the
    # mapping and getenv do not see it (matches CPython).
    os.putenv("TPY_ENV_B", "2")
    print("TPY_ENV_B" in os.environ)            # False
    print(os.getenv("TPY_ENV_B") is None)       # True

    # unsetenv is the same footgun in reverse: libc-only, leaves the snapshot
    # alone, so the key set through the mapping stays visible (matches CPython).
    os.environ["TPY_ENV_C"] = "3"
    os.unsetenv("TPY_ENV_C")
    print("TPY_ENV_C" in os.environ)            # True

    # Missing key raises KeyError (catchable).
    try:
        _ = os.environ["TPY_ENV_NOPE"]
    except KeyError:
        print("getitem KeyError")
    try:
        del os.environ["TPY_ENV_NOPE"]
    except KeyError:
        print("delitem KeyError")


main()
