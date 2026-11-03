# A JSON @model of a cycle member stores a later peer's enum: the class macro
# sees the enum's members while a_user registers.
import a_user
from z_peer import E


def main() -> None:
    c = a_user.C(9, E.B)
    print(a_user.dump(c))
    c.e = E.A
    print(a_user.dump(c))


main()
