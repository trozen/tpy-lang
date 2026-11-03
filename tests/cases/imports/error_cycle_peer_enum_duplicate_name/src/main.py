# A later cycle peer's enum that repeats a member name is rejected at the
# enum, although a peer read the enum first.
import a_user
import z_peer


def main() -> None:
    print(a_user.C(1).n)


main()
