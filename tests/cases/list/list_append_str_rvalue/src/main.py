# list[str] with rvalue string_view sources (slice / function return) through
# append, insert, __setitem__. Regression: rvalue str_view reaching an Own[str]
# param must materialize to std::string; the ARG-context strview_to_str
# coercion used to skip the wrap for rvalues (lvalues went through a different
# codegen path that materialized correctly).

def first_word(s: str) -> str:
    return s[:5]

def main() -> None:
    items: list[str] = []
    subject = "hello world"
    items.append(subject[:5])
    items.append(subject[6:])
    items.append(first_word(subject))
    items.insert(1, subject[4:5])
    items[0] = subject[6:]
    print(items)

main()
