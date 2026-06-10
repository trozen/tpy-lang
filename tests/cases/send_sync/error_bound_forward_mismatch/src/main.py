# A Send-bounded type parameter does NOT satisfy a Sync bound when forwarded:
# the marker forward-conformance is reflexive, not cross-marker.
from tpy import Own, Send, Sync


def needs_sync[T: Sync](x: Own[T]) -> None:
    print("sync")


def forward[T: Send](x: Own[T]) -> None:
    needs_sync[T](x)  # tpyc: error(/does not satisfy bound 'Sync'/)


def main() -> None:
    forward(5)


main()
