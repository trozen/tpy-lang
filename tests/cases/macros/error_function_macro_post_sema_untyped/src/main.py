# A deferred (post-sema) function macro rewrites an initializer without typing
# the replacement, so the local's type has no source left. Pins the diagnostic
# that rejects an un-analyzed node reaching codegen in binding position.
from untypedmod import untyped_rewrite
from tpy import Int32


def sentinel(a: Int32, b: Int32) -> Int32:
    return 0


@untyped_rewrite
def combine(a: Int32, b: Int32) -> Int32:
    # The rewritten initializer carries no recorded type, so the slot type is
    # undeducible here -- not at the return below.
    x = sentinel(a, b)  # tpyc: error(/Could not infer type for variable 'x'/)
    return x


def main() -> None:
    print(combine(3, 4))


main()
