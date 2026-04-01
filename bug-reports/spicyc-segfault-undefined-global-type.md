# `spicyc` segfaults on undefined type in global type declaration

Compiling the next spicy file with `spicyc -j` causes a segmentation fault instead of reporting a compiler error.
```spicy
module Segfault;

# Wrong type declaration
type Foo = uint;

public type Main = unit {
  foo: Foo;
};
```

The crash is triggered by the `type Foo = uint;` global type declaration because `uint` is not a valid type.

## Crash reproduction

Save the above snippet of spicy code as `seg-fault.spicy` and compile it.
```bash
spicyc -j seg-fault.spicy
```

The compiler exits and prints no diagnostic:

```text
Segmentation fault (core dumped)
```

The same happens for `spicy-driver` and `spicy-dump`.

## Expected result

The compiler should emit a normal semantic error for the undefined type instead of crashing, like the one
obtained from the same type error but inside a `unit`:

```spicy
module SegfaultFixed;

public type Main = unit {
  # This will correctly emit a compiling error
  bar: uint;
};
```

Then compiling the adjusted file with `spicyc -j` produces a regular error for the remaining invalid type on `bar`:

```text
[error] seg-fault-fixed.spicy:5:3-5:12: unknown ID 'uint'
[error] spicyc: aborting after errors
```
