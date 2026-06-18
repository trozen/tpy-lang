# A bare capture of a storage-form Optional FIELD subject aliases the field's
# value (the is_storage_form_optional_source branch of the pointer-subject fix).
class Box:
    def __init__(self, v: int):
        self.v = v

class Holder:
    def __init__(self, opt: Box | None):
        self.opt = opt

def main():
    h = Holder(Box(1))
    match h.opt:
        case q:
            q.v = 99  # tpyc: warning(/Potential None access/)
    if h.opt is not None:
        print(h.opt.v)   # 99 -- q aliased the field's Box

main()
