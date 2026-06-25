# The urlparse result is a record too: integer indexing is a compile error
# (sibling of error_urllib_parse_index for SplitResult).
from urllib.parse import urlparse


def main() -> None:
    q = urlparse("http://h/path")
    print(q[0])  # tpyc: error(/Cannot index type ParseResult: no __getitem__/)


main()
