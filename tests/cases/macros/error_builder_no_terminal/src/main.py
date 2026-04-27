# A builder ctor must be closed by exactly one @builder_terminal call;
# letting the trace fall off the end of the function is a hard error.
from tpy import Int32
from _smoke_builder import Config


def main() -> Int32:
    cfg_builder = Config()  # tpyc: error(/never closed by a @builder_terminal/)
    cfg_builder.add("a", "1")
    return 0


main()
