# urllib.parse.urljoin: RFC 3986 relative-reference resolution -- relative/abs
# segments, "."/".." (incl. past-root), query/fragment-only, scheme-relative.
from urllib.parse import urljoin


def main() -> None:
    print(urljoin("http://h/a/b/c", "d"))
    print(urljoin("http://h/a/b/", "d"))
    print(urljoin("http://h/a/b/c", "../d"))
    print(urljoin("http://h/a/b/c", "../../d"))
    print(urljoin("http://h/a/b/c", "../../../../d"))   # ".." past root clamps
    print(urljoin("http://h/a/b/c", "."))
    print(urljoin("http://h/a/b/c", "./d"))
    print(urljoin("http://h/a/b/c", "/abs/path"))
    print(urljoin("http://h/a/b/c", "?just=query"))
    print(urljoin("http://h/a/b/c", "#frag"))
    print(urljoin("http://h/a/b/c", ""))
    print(urljoin("http://h/a/b/c", "//other/x"))
    print(urljoin("http://h/a/b/c", "https://full/url"))
    print(urljoin("", "rel"))
    print(urljoin("http://base/x", ""))
    print(urljoin("http://h/a//b/c", "g"))              # collapses // in middle
    print(urljoin("file:///etc/", "hosts"))             # non-http scheme resolves


main()
