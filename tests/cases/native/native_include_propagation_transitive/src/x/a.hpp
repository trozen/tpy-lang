#pragma once

namespace xcore {

struct B {
    bool flag = false;
};

struct A {
    B inner;
};

}  // namespace xcore
