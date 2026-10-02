/**
 * print(*xs) self-check: PrintJoin / PrintEach.
 *
 * Pins the separator rule (one written-flag spans every segment of the
 * chain, so an empty segment writes neither items nor separators) and that
 * a segment borrows its sequence: every source kind the compiler hands a
 * `*xs` segment -- a vector, an inline std::array, a span and both
 * tpy::varargs storage modes, each as an lvalue and, where one can be
 * spelled, as a temporary -- is walked in place, with no element copied.
 * The temporary-stream sink a `file=` Writable gets is covered too.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <array>
#include <cstdio>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

#include "tpy/tpy.hpp"

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

int copies = 0;

// An element that counts its copies: a segment that copied its source, or
// an element on the way to the writer, shows up here.
struct Counted {
    int v;
    explicit Counted(int x) : v(x) {}
    Counted(const Counted& o) : v(o.v) { ++copies; }
    Counted(Counted&& o) noexcept : v(o.v) {}
    Counted& operator=(const Counted& o) { v = o.v; ++copies; return *this; }
    Counted& operator=(Counted&&) noexcept = default;
};

std::ostream& operator<<(std::ostream& os, const Counted& c) {
    return os << c.v;
}

std::vector<int> make_vec() { return {7, 8}; }

std::array<int, 2> make_arr() { return {5, 6}; }

// A user `file=` sink: as_ostream hands back a TEMPORARY stream over it.
struct Sink {
    std::string text;
    int32_t write(std::string_view s) {
        text.append(s);
        return static_cast<int32_t>(s.size());
    }
    void flush() {}
};

std::string run_ints(std::string_view sep, const std::vector<int>& a,
                     const std::vector<int>& b, bool lead, bool tail) {
    std::ostringstream os;
    ::tpy::PrintJoin j(os, sep);
    j << ::tpy::PrintEach(a);
    if (lead) j << "L";
    j << ::tpy::PrintEach(b);
    if (tail) j << "T";
    j << ::tpy::print_join_end << "\n";
    return os.str();
}

}  // namespace

int main() {
    const std::vector<int> none;
    const std::vector<int> xs{3, 1, 2};

    // Separators: only between written items, across segments.
    check(run_ints(" ", xs, none, false, false) == "3 1 2\n", "one segment");
    check(run_ints(" ", none, none, false, false) == "\n", "all empty");
    check(run_ints(", ", none, none, true, true) == "L, T\n",
          "empty segments write no separator");
    check(run_ints(", ", xs, xs, true, false) == "3, 1, 2, L, 3, 1, 2\n",
          "segments on both sides of an item");
    check(run_ints("", xs, none, false, true) == "312T\n", "empty separator");
    {
        std::ostringstream os;
        ::tpy::PrintJoin(os, " ") << "" << ::tpy::PrintEach(xs)
                                  << ::tpy::print_join_end;
        check(os.str() == " 3 1 2", "an empty item still counts as written");
    }

    // Source kinds, lvalue and temporary.
    {
        std::ostringstream os;
        std::array<int, 3> arr{4, 5, 6};
        std::span<const int> sp(xs);
        ::tpy::PrintJoin(os, "|")
            << ::tpy::PrintEach(arr) << ::tpy::PrintEach(sp)
            << ::tpy::PrintEach(make_vec()) << ::tpy::PrintEach(make_arr())
            << ::tpy::print_join_end;
        check(os.str() == "4|5|6|3|1|2|7|8|5|6", "array, span, temporaries");
    }
    {
        std::ostringstream os;
        std::array<int, 2> direct{1, 2};
        ::tpy::varargs<int> vd(direct);
        ::tpy::PrintJoin(os, " ") << ::tpy::PrintEach(vd) << ::tpy::print_join_end;
        check(os.str() == "1 2", "value varargs");
    }

    // No copies: the source is borrowed and each element reaches the writer
    // by reference, through the default writer and a formatter alike --
    // including both storage modes of a reference-type *args pack.
    {
        std::vector<Counted> cs;
        cs.emplace_back(1);
        cs.emplace_back(2);
        std::array<Counted, 2> ca{Counted(3), Counted(4)};
        Counted a(5);
        Counted b(6);
        std::array<Counted*, 2> indirect{&a, &b};
        ::tpy::varargs<Counted> vd(ca);
        ::tpy::varargs<Counted> vi(indirect);
        copies = 0;
        std::ostringstream os;
        ::tpy::PrintJoin(os, " ")
            << ::tpy::PrintEach(cs) << ::tpy::PrintEach(ca)
            << ::tpy::PrintEach(cs, [](std::ostream& o, const auto& e) { o << e.v * 10; })
            << ::tpy::PrintEach(vd) << ::tpy::PrintEach(vi)
            << ::tpy::print_join_end;
        check(os.str() == "1 2 3 4 10 20 3 4 5 6", "counted elements print");
        check(copies == 0, "no element or source copy");
    }

    // A temporary sink stream lives through the whole chain.
    {
        Sink sink;
        ::tpy::PrintJoin(::tpy::as_ostream(sink), "-")
            << ::tpy::PrintEach(xs) << ::tpy::print_join_end << "\n";
        check(sink.text == "3-1-2\n", "temporary sink stream");
    }

    if (failures == 0) std::printf("OK\n");
    return failures == 0 ? 0 : 1;
}
