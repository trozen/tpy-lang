# A post-sema deferred macro calling note_param_mutated with an out-of-range
# param index must error rather than corrupt the host's mutation facts.
from oobmod import bad_note
from tpy import int32


def sentinel(c: int32) -> int32:
    return c


@bad_note
def f(c: int32) -> int32:  # tpyc: error(/note_param_mutated: param index 5 out of range/)
    return sentinel(c)


def main() -> None:
    print(f(0))


main()
