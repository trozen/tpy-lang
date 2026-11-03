# A field default naming a missing member of a later cycle peer's enum is
# rejected at the default, with the text it has outside a cycle.
import a_user
import z_peer


def main() -> None:
    print(a_user.C(1).n)


main()
