#pragma once
#include <cstdint>

namespace mylib {

struct Q {
    bool flag;
};

struct A {
    Q q;
};

struct S {
    A a;
};

}  // namespace mylib

inline mylib::S* get_s() {
    static mylib::S s{{{false}}};
    return &s;
}
