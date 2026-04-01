# `spicyc` segfaults on incorrect field type in global type declaration

Compiling the following spicy file with `spicyc -j` causes a segmentation fault due to the invalid uint field type in the type Foo definition 

```spicy
module Segfault;

# Wrong type declaration
type Foo = uint;

public type Main = unit {
  foo: Foo;
};
```

```bash
spicyc -j seg-fault.spicy
Segmentation fault (core dumped)
```

The same happens for `spicy-driver` and `spicy-dump`.

Instead of crashing, the compiler should emit a syntax error for the undefined type, like the one obtained from the same type error but inside a `unit`:

```spicy
module NoSegfault;

public type Main = unit {
  bar: uint;
};
```

```text
spicyc -j no-seg-fault.spicy
[error] no-seg-fault.spicy:5:3-5:12: unknown ID 'uint'
[error] spicyc: aborting after errors
```

