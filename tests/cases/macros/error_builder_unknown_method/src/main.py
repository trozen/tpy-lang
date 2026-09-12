# Calling a method on a tracked builder symbol that has no registered
# @builder_method handler is a hard error.
from tpy import int32
from _smoke_builder import Config


def main() -> int32:
    cfg_builder = Config()
    cfg_builder.frob()  # tpyc: error(/no @builder_method handler/)
    cfg_builder.build()
    return 0


main()
