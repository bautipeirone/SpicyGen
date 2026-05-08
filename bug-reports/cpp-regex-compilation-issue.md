# C++ compilation fails when compiling a specific regex with `spicyc`

Compiling the following code throws a C++ compilation error. Note that after the `const CRLF = /` comes a `\r\n` sequence.
```spicy
module Test;

const CRLF = /
/;

public type Main = unit {
    test: CRLF[];
};
```

```
=== Uh oh ... C++ compilation failed ===

This is unexpected and should not happen. Please help us fix the issue
by opening a ticket at https://github.com/zeek/spicy/issues/new.

Please include the content of the following file with your report:

  "/spicy-playground/hilti-jit-error.log"

That file contains diagnostics from the C++ compiler. Please also
include your Spicy code that triggered this error, ideally reduced to
a small snippet still exhibiting the issue if you can.

===
```

The output logs are the following
```
c++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0
Copyright (C) 2023 Free Software Foundation, Inc.
This is free software; see the source for copying conditions.  There is NO
warranty; not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

HILTI version 1.15.0 (6a826725)

## [job 3] /usr/bin/c++ -c -I/opt/spicy/include -fPIC -std=c++20 -g -O3 -DNDEBUG -fvisibility=hidden -Wno-invalid-offsetof -I/opt/spicy/include -Werror -Wno-stringop-overflow -Wno-array-bounds -Wno-restrict -Wno-maybe-uninitialized -Werror -Wno-stringop-overflow -Wno-array-bounds -Wno-restrict -Wno-maybe-uninitialized -o /tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc_ff2951b90e764573.o /tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc

/tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc:216:23: error: multi-character character constant [-Werror=multichar]
  216 | / (regexp) (container 'test');
      |                       ^~~~~~
/tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc:262:23: error: multi-character character constant [-Werror=multichar]
  262 | / (regexp) (container 'test');
      |                       ^~~~~~
/tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc: In member function ‘hilti::rt::Tuple<hilti::rt::stream::View, SafeInt<long int, hilti::rt::integer::detail::SafeIntException>, hilti::rt::stream::SafeConstIterator, hilti::rt::Optional<hilti::rt::RecoverableFailure> > hlt_internal::Test::Main::_t_parse_test_stage1(hilti::rt::ValueReference<hilti::rt::Stream>&, const hilti::rt::stream::SafeConstIterator&, hilti::rt::stream::View, hilti::rt::Bool, hilti::rt::integer::safe<long int>, hilti::rt::stream::SafeConstIterator, hilti::rt::Optional<hilti::rt::RecoverableFailure>, hilti::rt::Vector<hilti::rt::Bytes>&)’:
/tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc:216:1: error: expected primary-expression before ‘/’ token
  216 | / (regexp) (container 'test');
      | ^
/tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc:216:4: error: ‘regexp’ was not declared in this scope
  216 | / (regexp) (container 'test');
      |    ^~~~~~
/tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc:216:4: note: suggested alternatives:
In file included from /opt/spicy/include/hilti/rt/logging.h:9,
                 from /opt/spicy/include/hilti/rt/libhilti.h:22,
                 from /opt/spicy/include/spicy/rt/libspicy.h:5,
                 from /tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc:6:
/opt/spicy/include/hilti/rt/global-state.h:28:11: note:   ‘hilti::rt::regexp’
   28 | namespace regexp::detail {
      |           ^~~~~~
In file included from /opt/spicy/include/hilti/rt/libhilti.h:26:
/opt/spicy/include/hilti/rt/type-info.h:1812:23: note:   ‘hilti::rt::type_info::regexp’
 1812 | extern const TypeInfo regexp;
      |                       ^~~~~~
/tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc:216:13: error: ‘container’ was not declared in this scope
  216 | / (regexp) (container 'test');
      |             ^~~~~~~~~
/tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc:262:1: error: expected primary-expression before ‘/’ token
  262 | / (regexp) (container 'test');
      | ^
/tmp/Test_ff2951b90e764573-4ae6bc82e8969644.cc:262:22: error: expected ‘)’ before '\x74657374'
  262 | / (regexp) (container 'test');
      |            ~         ^~~~~~~
      |                      )
cc1plus: all warnings being treated as errors
```

This appears to be some undefined behavior, since for example if the field `test` is of type `CRLF` instead of `CRLF[]`, then the error does not show up.
It would be nice to fix it and output a better error report from `spicyc`.