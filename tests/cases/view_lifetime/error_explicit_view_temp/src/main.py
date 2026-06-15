# An explicit view annotation bound to a temporary is rejected: the temporary
# dies at end-of-statement so the view would dangle. (StrView shown; BytesView is
# the same path -- error_explicit_bytesview_temp.) An inferred local promotes
# instead; the explicit view asks for a view, so annotate owned `str` to copy.
from tpy import StrView


def make() -> str:
    return "this is long enough to dodge the small-string buffer"


def from_owned_temp() -> None:
    v: StrView = make()  # tpyc: error(/temporary view source/)
    print(v)


def main() -> None:
    from_owned_temp()


main()
