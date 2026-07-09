# Value-repr Optional[str] return sinks routed through THIR: None -> nullopt, a
# str literal -> bare, and a whole Optional[str] param -> the view->owned shim.


def pick(b: bool) -> str | None:
    if b:
        return "yes"
    return None


def forward(s: str | None) -> str | None:
    return s


def main() -> None:
    print(pick(True))
    print(pick(False) is None)
    print(forward("hi"))
    print(forward(None) is None)


main()
