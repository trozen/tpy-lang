# A guarded reference-capture arm aliases the subject on both guard-pass and
# guard-fail->next-arm paths; a scalar guarded capture copies (value semantics).
class Box:
    def __init__(self, v: int):
        self.v = v

def main():
    a = Box(10)
    match a:
        case q if q.v > 5:
            q.v = 99       # guard passes -> mutate via capture
        case r:
            r.v = 1
    print(a.v)             # 99

    c = Box(3)
    match c:
        case q if q.v > 5:
            q.v = 99
        case r:
            r.v = 1        # guard failed -> next arm aliases c
    print(c.v)             # 1

    n = 7
    match n:
        case m if m > 5:
            print(m)       # 7 (scalar capture copies)
        case other:
            print(other)

main()
