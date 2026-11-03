# A later cycle peer's enum that repeats a value is rejected at the enum,
# although a peer read the enum first.
# CPython makes the repeat an alias (BUGS.md#enum-alias-member-rejected).
import a_user
import z_peer


def main() -> None:
    print(a_user.C(1).n)


main()
