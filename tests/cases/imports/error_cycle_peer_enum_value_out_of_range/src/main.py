# A later cycle peer's int8 enum with a value out of range is rejected at
# the member, although a peer read the enum first.
import a_user
import z_peer


def main() -> None:
    print(a_user.C(1).n)


main()
