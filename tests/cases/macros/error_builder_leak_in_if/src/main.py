# A tracked builder symbol cannot be referenced inside a control-flow
# block (if/while/for/try) because the trace must be linear.
from tpy import int32
from _smoke_builder import Config


def main() -> int32:
    cfg_builder = Config()
    if True:
        cfg_builder.add("a", "1")  # tpyc: error(/inside a control-flow block/)
    cfg_builder.build()
    return 0


main()
