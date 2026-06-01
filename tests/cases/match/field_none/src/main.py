# `field=None` on a record subject tests the field's storage repr (optional
# -> has_value, union-with-None -> monostate); a non-None value falls through.
class W:
    opt: "str | None"
    uni: "int | str | None"
    def __init__(self, opt: "str | None", uni: "int | str | None") -> None:
        self.opt = opt
        self.uni = uni

def describe(w: W) -> str:
    match w:
        case W(opt=None):  # tpyc: ok
            return "opt-none"
        case W(uni=None):  # tpyc: ok
            return "uni-none"
        case _:
            return "both-set"

def main() -> None:
    print(describe(W(None, 1)))
    print(describe(W("x", None)))
    print(describe(W("x", 1)))

main()
