/**
 * str.join self-check: tpy::str_join.
 *
 * Pins the contents for every source shape and separator, that a join over
 * strings stored in memory (vector, array, span, initializer list; str,
 * String or string_view elements) allocates its result once, and that any
 * other source -- a lazy view, a node container, C strings, a string
 * subclass with its own conversion -- is walked exactly once. A user
 * contiguous range is presized without a second begin().
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <array>
#include <cstdio>
#include <cstdlib>
#include <list>
#include <new>
#include <ranges>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "tpy/tpy.hpp"

namespace {

int failures = 0;
bool counting = false;
int allocations = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

template<typename Source>
int join_allocations(const Source& items, std::string_view sep, std::string_view expected) {
    allocations = 0;
    counting = true;
    std::string joined = tpy::str_join(sep, items);
    counting = false;
    check(joined == expected, "joined contents");
    return allocations;
}

std::string repeat_joined(const std::string& piece, int n, std::string_view sep) {
    std::string out;
    for (int i = 0; i < n; ++i) {
        if (i) out.append(sep);
        out.append(piece);
    }
    return out;
}

// A string subclass with its own view conversion: not an exact element type,
// so it takes the single pass and converts once.
struct Converting : std::string {
    mutable int calls = 0;
    operator std::string_view() const {
        ++calls;
        return calls == 1 ? "first" : "second";
    }
};

// A contiguous, sized user range whose begin() counts its calls.
struct CountedView : std::ranges::view_interface<CountedView> {
    std::string words[2]{"a", "b"};
    mutable int begins = 0;
    const std::string* begin() const { ++begins; return words; }
    const std::string* end() const { return words + 2; }
    std::size_t size() const { return 2; }
};

}  // namespace

void* operator new(std::size_t n) {
    if (counting) ++allocations;
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }

int main() {
    // Contents: empty source, singleton, empty separator, empty pieces.
    check(tpy::str_join(", ", std::vector<std::string>{}).empty(), "empty source");
    check(tpy::str_join(", ", std::vector<std::string>{"a"}) == "a", "singleton");
    check(tpy::str_join("", std::vector<std::string>{"a", "b", "c"}) == "abc", "empty separator");
    check(tpy::str_join("-", std::vector<std::string_view>{"", "x", ""}) == "-x-", "empty pieces");
    check(tpy::str_join("|", {"p", "q"}) == "p|q", "braced C strings");
    check(tpy::str_join("|", {std::string_view("p"), std::string_view("q")}) == "p|q",
          "braced string views");

    // Stored strings: one allocation for a result past the inline buffer.
    const std::string piece(40, 'x');
    const std::string long_joined = repeat_joined(piece, 64, "::");
    std::vector<std::string> strs(64, piece);
    std::vector<tpy::String> owned(64, tpy::String(piece));
    std::vector<std::string_view> views(64, std::string_view(piece));
    std::array<std::string, 3> arr{piece, piece, piece};
    check(join_allocations(strs, "::", long_joined) == 1, "vector<str> allocates once");
    check(join_allocations(owned, "::", long_joined) == 1, "vector<String> allocates once");
    check(join_allocations(views, "::", long_joined) == 1, "vector<string_view> allocates once");
    check(join_allocations(std::span<const std::string>(strs), "::", long_joined) == 1,
          "span allocates once");
    check(join_allocations(arr, "::", repeat_joined(piece, 3, "::")) == 1,
          "array allocates once");

    const std::string_view pv(piece);
    allocations = 0;
    counting = true;
    std::string braced = tpy::str_join("::", {pv, pv, pv});
    counting = false;
    check(allocations == 1, "braced string views allocate once");
    check(braced == repeat_joined(piece, 3, "::"), "braced string views contents");

    // A short result stays in the inline buffer.
    check(join_allocations(std::vector<std::string_view>{"a", "b"}, ",", "a,b") == 0,
          "short join allocates nothing");

    // Other sources are walked once: a lazy view's conversion runs per item.
    int calls = 0;
    auto lazy = views | std::views::take(3) |
                std::views::transform([&](std::string_view s) { ++calls; return s; });
    check(tpy::str_join("::", lazy) == repeat_joined(piece, 3, "::"), "lazy view contents");
    check(calls == 3, "lazy view walked once");
    std::list<std::string> nodes{"a", "b", "c"};
    check(tpy::str_join("/", nodes) == "a/b/c", "node container");
    std::vector<Converting> converting(1);
    check(tpy::str_join(",", converting) == "first", "subclass conversion contents");
    check(converting[0].calls == 1, "subclass conversion runs once");
    CountedView counted;
    check(tpy::str_join(",", counted) == "a,b", "user contiguous range contents");
    check(counted.begins == 1, "user contiguous range begins once");

    if (failures == 0) std::printf("OK\n");
    return failures == 0 ? 0 : 1;
}
