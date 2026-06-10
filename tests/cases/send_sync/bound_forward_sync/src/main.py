# A Sync-bounded type parameter satisfies a Sync bound when forwarded to a
# nested generic, mirroring the Send case (the fix is symmetric).
from tpy import Own, Sync


def sink[T: Sync](x: Own[T]) -> None:
    print("sync sink")


def forward[T: Sync](x: Own[T]) -> None:
    sink(x)


def main() -> None:
    forward(5)


main()
