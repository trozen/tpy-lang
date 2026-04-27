# A @builder_returns method cannot reassign into a name that was
# already a tracked builder symbol -- otherwise the parent's trace
# state would be silently overwritten.
from tpy import Int32
from _smoke_builder import Config


def main() -> Int32:
    cfg = Config()
    cfg = cfg.section()  # tpyc: error(/builder-trace symbol 'cfg' cannot be reassigned/)
    return 0


main()
