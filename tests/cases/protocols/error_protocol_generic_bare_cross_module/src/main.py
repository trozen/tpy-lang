# Using a cross-module user-defined generic protocol without type
# arguments must surface the same diagnostic as the same-module/stdlib
# case. Pins Phase F.5.2/F.5.3 resolver behaviour: the canonical-import
# path consults TypeDef.protocol and raises SemanticError (not
# ParseError) so the CLI emits `file:line: error: ...` format.
from tpy import int32
from box_proto import BoxLike


def bad(b: BoxLike) -> int32:  # tpyc: error(/Generic protocol.*requires type arguments/)
    return b.size()


def main() -> None:
    pass


main()
