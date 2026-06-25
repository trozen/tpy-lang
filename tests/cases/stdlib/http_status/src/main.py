# http.HTTPStatus is a plain IntEnum: numeric .value, .name, value-lookup, and
# int comparison. (CPython's .phrase/.description/.is_* are absent -- TPy enums
# can't carry per-member data; using them is a compile error, not tested here.)
from http import HTTPStatus


def classify(code: int) -> str:
    if code == HTTPStatus.OK.value:
        return "ok"
    if code == HTTPStatus.NOT_FOUND.value:
        return "missing"
    return "other"


def main() -> None:
    print(HTTPStatus.OK.value)              # 200
    print(HTTPStatus.NOT_FOUND.value)       # 404
    print(HTTPStatus.OK.name)               # OK
    print(HTTPStatus.INTERNAL_SERVER_ERROR.value)   # 500
    print(HTTPStatus(404).name)             # value lookup -> NOT_FOUND
    print(HTTPStatus(204).name)             # NO_CONTENT
    print(classify(200))
    print(classify(404))
    print(classify(503))
    print(200 == HTTPStatus.OK.value)       # True


main()
