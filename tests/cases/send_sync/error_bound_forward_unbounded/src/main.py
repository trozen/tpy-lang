# An UNBOUNDED type parameter does not satisfy a Send bound when forwarded:
# without its own Send bound, T could be instantiated with a non-Send type.
from tpy import Own, Send


def needs_send[T: Send](x: Own[T]) -> None:
    print("send")


def forward[T](x: Own[T]) -> None:
    needs_send[T](x)  # tpyc: error(/does not satisfy bound 'Send'/)


def main() -> None:
    forward(5)


main()
