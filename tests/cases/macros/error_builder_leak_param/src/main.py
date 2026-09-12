# A tracked builder symbol cannot be passed to an ordinary function --
# it has no runtime representation and exists only as compile-time state.
from tpy import int32
from _smoke_builder import Config


def consume(c: int32) -> int32:
    return c


def main() -> int32:
    cfg_builder = Config()
    cfg_builder.add("a", "1")
    consume(cfg_builder)  # tpyc: error(/builder-trace symbol 'cfg_builder' may only appear/)
    cfg_builder.build()
    return 0


main()
