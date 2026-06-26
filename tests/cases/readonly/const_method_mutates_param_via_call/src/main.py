# A const (readonly-inferred) method may mutate a reference-type PARAM -- not
# mutating `self` is independent of whether the method mutates its args. Each
# method below is const; we mutate a param and read it back after the call (the
# mutate-after-boundary rule -- proves the param is a mutable ref, not a copy).
class Sink:
    buf: str

    def __init__(self) -> None:
        self.buf = ""

    def push(self, s: str) -> None:
        self.buf = self.buf + s


def do_mutate(p: Sink, s: str) -> None:
    p.push(s)


class Renderer:
    # param mutated transitively via a mutating free function: `out` is `Sink&`
    def via_call(self, out: Sink) -> None:
        do_mutate(out, "call;")

    # param mutated via a direct method call: `out` is `Sink&`
    def via_method(self, out: Sink) -> None:
        out.push("method;")

    # only `out` mutated: `a` stays `const Sink&`
    def only_second(self, a: Sink, out: Sink) -> None:
        out.push(a.buf)
        out.push("second;")


def main() -> None:
    r = Renderer()

    s1 = Sink()
    r.via_call(s1)
    print(s1.buf)            # call;  -- transitive mutation crossed the boundary

    s2 = Sink()
    r.via_method(s2)
    print(s2.buf)            # method;

    src = Sink()
    src.push("src=")
    dst = Sink()
    r.only_second(src, dst)
    print(dst.buf)           # src=second;
    print(src.buf)           # src=  -- `a` was read, not mutated


main()
