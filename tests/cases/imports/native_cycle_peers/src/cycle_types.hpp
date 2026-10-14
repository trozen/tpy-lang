#pragma once
#include <cstdint>

namespace pair {
struct Inner { int32_t v = 3; };
inline int32_t LIMIT = 4;
struct Outer { Inner held; int32_t tag = 2; };
inline int32_t peek(const Outer& o) { return o.held.v + o.tag; }
}

namespace eng {
struct Msg { int32_t n = 5; };
struct Engine {
    int32_t bias = 0;
    int32_t take(const Msg& m) { return m.n + bias; }
};
}

namespace hubns {
struct Note { int32_t n = 1; };
struct Tag { int32_t k = 7; };
struct Hub {
    int32_t bias = 0;
    int32_t read_note(const Note& n) { return n.n + bias; }
    int32_t read_tag(const Tag& t) { return t.k + bias; }
};
}
