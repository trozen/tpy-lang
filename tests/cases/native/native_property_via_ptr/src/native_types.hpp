#pragma once
#include <cstdint>

namespace x {

struct A {
    int32_t v;
};

struct S {
private:
    A config_{123};
public:
    A& config() { return config_; }
    const A& config() const { return config_; }
};

}  // namespace x

inline x::S* get_s() {
    static x::S s;
    return &s;
}
