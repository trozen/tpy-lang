# A tracked builder symbol cannot be reassigned -- it has no runtime
# representation, so binding the name to anything else is a hard error.
from tpy import int32
from _smoke_builder import Config


def main() -> int32:
    cfg_builder = Config()
    cfg_builder.add("a", "1")
    cfg_builder = "bogus"  # tpyc: error(/cannot be reassigned/)
    cfg_builder.build()
    return 0


main()
