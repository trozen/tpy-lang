# A nested def that shadows a module function and binds the same name with
# `except ... as`. The binding makes the name the nested def's own local, and
# the handler's end deletes it, so the read after the handler is unbound
# (CPython raises UnboundLocalError) -- neither the module function nor the
# nested def itself.
from tpy import int32


def helper(x: int32) -> int32:
    return 4012


def main() -> None:
    def helper(x: int32) -> int32:
        if x > 100:
            return x
        try:
            raise ValueError("v")
        except ValueError as helper:
            pass
        # the handler unbound the nested def's own `helper`, so this read is unbound
        return helper(x + 200)  # tpyc: error(/variable 'helper' may not be assigned at this point/)

    print(helper(1))


main()
