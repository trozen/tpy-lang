# A member init whose source is a parameter the body later REASSIGNS: the init
# is demoted into the body, a placement the member-init cells do not carry.
class Named:
    name: str

    def __init__(self, name: str) -> None:  # tpyc: error(/ctor.param_reassign_copy/)
        self.name = name
        name = "other"
        print(name)


def main() -> None:
    print(Named("a").name)


main()
