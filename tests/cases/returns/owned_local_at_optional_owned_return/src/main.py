# Owned locals returned at an `owned | None` slot: a String local, a concat
# result, a bytes copy and an Own param each land in the optional's storage.
from tpy import Own, String


def from_local(k: str) -> str | None:
    if len(k) > 0:
        v = String(k)
        return v
    return None


def from_concat(k: str) -> str | None:
    if len(k) > 0:
        v = k + "x"
        return v
    return None


def from_bytes(k: bytes) -> bytes | None:
    if len(k) > 0:
        v = bytes(k)
        return v
    return None


def from_own_param(k: Own[str]) -> str | None:
    if len(k) > 0:
        return k
    return None


def main() -> None:
    print(from_local("a"), from_concat("a"))
    print(from_bytes(b"a"), from_own_param(String("a")))
    print(from_local(""), from_own_param(String("")))


main()
