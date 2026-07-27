# Grammar specification language
**Only use the tools explained in this document. It is STRICTLY prohibited to use syntax not declared here.**
**The name of the module MUST be <PROTOCOL_NAME>_<LLM_NAME>.**

## General guidelines
1. Generally, you define parsers through types called “units” that describe the syntax of a protocol. A set of units forms a grammar. In practice, units typically correspond pretty directly to protocol data units (PDUs) as protocol specifications tend to define them.
2. One will typically create a unit type for each of the main data units that a protocol defines.
3. There must be only one `public type` declaration in the entire grammar, which is the entry point for the main parser of the protocol.
4. Subunits don't have to repeat fields declared in the parent units.
5. Inside a unit, `self` references to the current unit, and its fields can be accessed via `self.field`, and `$$` references to the last read input, this can be used to perform conversions or conditional parsing.
6. Every field mentioned in the specification should be accessible by itself, not as a combination of fields like merging them into an integer type.

## Grammar
### Type declarations
* Every script starts with a `module` statement defining a namespace for the script’s content.
* Then, you have to import spicy with the line `import spicy;`.
* The layout of a piece of data is defined by creating a `unit` type. The type lists individual *fields* in the order they are to be parsed.
* A unit field can have another unit as its type. If the subunit receives parameters, they must be given right after the type. All units have to be declared globally, anonymous units are not allowed.
* Globally defined constants and regex are declared with the reserved keyword `const`, units are declared with `type` and the main unit with `public type`.
* Comment lines only start with the `#` symbol. Multiline comments are not supported.
* All blocks end with a `;`. The ONLY exception is blocks inside a `switch` statement. The `switch` itself has to end with a `;`.

### Fields
* Each field inside a unit has a type and an optional name. The type defines how that field will be parsed from raw input data. If a field has a name, it can later be referenced to access its value via `self.fieldName`. Consequently, all fields with semantic meanings have names, while those which are unlikely to be relevant later do not (e.g., whitespace). Fields like this are called anonymous, and can be used for special packet structures that do not correspond to packet fields (i.e. mandatory bytes at start of field, padding). You should always declare them like `: Type;`, this is, omit the name but not the colons.
* Fields of integer type can be either signed (`intN`) or unsigned (`uintN`). In either case, N determines the number of bytes being parsed.
* You can use `void` as type for empty fields (i.e. 0 byte fields).
* Fields may use the `&convert=EXPR` attribute to transform the read input. Inside the expression, `self` can be used to reference the state of the unit (i.e. `self.opcode`) and `$$` references to the read input that will be transformed (i.e. `&convert=$$.to_uint(16)`). It is very common to use `&convert` to cast a field into an enum like `op: uint8 &convert=Opcode($$)`.
* Conditional fields are implemented by appending an `if(COND)` to the field declaration.
* Fields can have **constraints** on their values. The following example requires `x` to be greater than 2 and less than 5:
```
type Foo = unit {
    x: int8 &requires=(2 < $$ && $$ < 5);
};
```
* The **size** attribute specifies the size in bytes a field must consume. The value for the size attribute can reference unit parameters or previous field's values.
```
public type Foo = unit {
    l: int8;
    x: int8 &size=self.l;
    y: bytes &eod;
};
```
* Fields that represent IP addresses should be parsed like this: `host: addr &ipv4;`. The same works for IPv6. For arrays of addresses, the correct syntax is `(addr &ipv4)[N]`.
* Fields that are units of time (seconds, minutes, etc.) must be casted with `&convert=cast<interval>($$)`. Note that the input to `cast<interval>` is in seconds.

### Bytes
* When parsing a field of type `bytes`, raw input bytes will be consumed according to a specified attribute that determines when to stop. The following attributes are supported, and at least one of these attributes must be provided:
  * `&eod`: Consumes all subsequent data until the end of the input is reached.
  * `&size=N`: Consumes exactly N bytes. The attribute may be combined with &eod to consume up to N bytes instead (i.e., permit running out of input before the size limit is reached).
  * `&until=DELIM`: Consumes bytes until the specified delimiter is found. DELIM must be of type bytes itself. The delimiter will not be included into the resulting value, but consumed.
  hint: since `&until=DELIM` consumes the delimiter value at the end of the field, there is no need to add the delimiter as a separate follow-up field.
* Fields that have a fixed size of N bytes but contain null terminated strings should be declared like this:
`bytes &size=N &convert=$$.split1(b"\0")[0].decode();`. This will only retain the significant part of the field.
* To convert raw bytes to an integer with a given byte order, use `&convert=$$.to_uint(spicy::ByteOrder::Big)` (`Little` respectively for little endian). This way one
can parse bytes with a non-power of two size, like 3 byte integers.

### Regular Expressions
Spicy provides POSIX-style regular expressions. Regular expression are typically of the form `/PATTERN/`, where `PATTERN` is a regular expression to match. In addition, regular expression constants can also consist of multiple patterns, separated by `|`. This creates a single regular expression constant that matches any of the patterns.
Regular expression patterns use the extended POSIX syntax, with a few smaller differences and extensions:
* Supported character classes are: `[:lower:]`, `[:upper:]`, `[:digit:]`, `[:blank:]`. Note that `[:lower:]` and `[:upper:]` do not take locales into account, they only match standard ASCII characters.
* `\b` asserts a word-boundary, `\B` matches asserts no word boundary.
* `\xXX` matches a byte with the binary hex value XX (e.g., `\xff` matches a byte of decimal value 255).
These are examples of useful regex patterns.
```
const Token = /[^ \t\r\n]+/;
const NewLine = /\r?\n/;
const OptionalWhiteSpace = /[ \t]*/;
```
In the following example, `bar` is a **constant field** that only matches `"bar"`, whereas foo matches the assigned regular expression. Fields that match a **regex** get assigned the type `bytes` and the value of what matches the regex.
```
const Baz = b"baz";
public type Foo = unit {
    bar: b"bar";
    foo: /Foo.*/;
    baz: Baz;
};
```

### Vectors
* Parsing a vector creates a loop that repeatedly parses elements of the specified type from the input stream until an end condition is reached. The syntax for a known size vector is `NAME : ELEM_TYPE[SIZE]`. It is possible to skip the SIZE and instead use one of the following end conditions to terminate a vector’s parsing loop:
  * If the end of the vector is determined by a specific value (like constant, delimeter, or regex) just add this value as the next field.
    For example, if the array of Foo objects finishes when the "abc" delimeter is found, then the correct solution is
    ```
    # Previous fields ...
    foos: Foo[];
        : b"abc";
    # Next fields
    ```
  * &eod: Parses elements until the end of the input stream is reached.
  * &size=N: Parses the vector from the subsequent N bytes of input data.
  * &until=COND: Parses elements until COND becomes true for the most recently parsed element. Inside the condition, `$$` references that element.

### Parser state, hooks, and structural constraints
Use unit variables when the parser must remember information that is not a direct input field, such as a derived payload length, whether an optional section is present, a selected payload format, a checksum seen in a footer, or values extracted from an options list. Variables are declared inside a unit with `var`:
```
type Frame = unit {
    header: Header(self);
    payload: bytes &size=self.payload_length if ( self.has_payload );

    var has_payload: bool = False;
    var payload_length: uint64 = 0;
};
```
Use `&optional` on a variable when the value may not have been seen yet. Check for presence with `self?.field` before reading it:
```
type Frame = unit {
    header: Header(self);
    payload: bytes &size=self.payload_length if ( self?.payload_length );

    var payload_length: uint64 &optional;
};
```
Units can receive parameters. Use `inout` when a subunit must update state in its parent while it parses. This is useful for structural constraints like "if this option appears, the following payload is compressed" or "if this flag is set, parse a trailer":
```
type Header = unit(inout frame: Frame) {
    magic: b"\x12\x34";
    flags: uint8;
    length: uint16;

    on length {
        frame.payload_length = self.length;
        frame.has_payload = self.length > 0;
    }
};
```
Use `on FIELD { ... }` hooks to react once a field has been parsed, and `on %init { ... }` to initialize state from unit parameters before fields parse. Use hooks to derive state before later fields use it in `if`, `&size`, `&until`, `switch`, or `&requires`:
```
type Frame = unit(default_payload: bool) {
    header: Header(self);
    payload: Payload(self) if ( self.has_payload );

    var has_payload: bool;
    var payload_length: uint64 = 0;
    var compressed: bool = False;

    on %init {
        self.has_payload = default_payload;
    }
};

type Payload = unit(inout frame: Frame) {
    data: bytes &size=frame.payload_length;
};
```
For structural constraints, prefer deriving explicit parser state and then using that state in later fields. Examples include validating magic values, enforcing version ranges with `&requires`, checking that a length field controls exactly the following payload, choosing a payload subunit from a type field, or making an optional footer depend on a flag.

When a condition is invalid for the protocol and the packet must be rejected, use `throw "error message";` from a hook or a switch branch. Use `if` only for optional fields that are legitimately absent; do not use `if` to silently accept malformed packets. Use `&requires` for simple field-local constraints, and use `throw` when the condition depends on derived state or needs custom logic.
```
type Frame = unit {
    version: uint8 &requires=($$ = 1);
    flags: uint8;
    length: uint16;
    payload: bytes &size=self.length if ( self.length > 0 );

    on length {
        if ( (self.flags & 0xf0) != 0 )
            throw "reserved flag bits are set";
    }
};
```

## Important considerations
* For multi-byte integer values, it is important to consider the byte order for parsing. Network byte order (i.e., big-endian) is the default. To change byte order, use field attributes. Add `&byte-order=spicy::ByteOrder::Little;` when you need to parse an integer as little endian.
* The following words are reserved and cant be used as field names: file, port, type, time. The following can be used to replace them: filename, port_number, type_value, time_value.
* Booleans are denoted `True` and `False`.

## Examples
* **switch** constructs are supported as a way to branch into one of several parsing alternatives.
```
public type Foo = unit {
    x: bytes &size=1;
    switch ( self.x ) {
        b"A" -> a8: int8;
        b"B" -> a16: int16;
        b"C" -> : void;
        * -> {
          u8: uint8;
          u16: uint16;
          u32: uint32;
        } # This block doesnt carry a `;`
    }; # The block for the switch does carry a `;`
};
```
They can also be specified without a condition, and the branch is decided by look-ahead on the input stream. This mechanism becomes powerful once you have subunits that are recognizable by how they start:
```
type A = unit {
    a: b"A";
};

type B = unit {
    b: uint16(0xffff);
};

public type Foo = unit {
    switch {
        -> a: A;
        -> b: B;
    };
};
```
---
* **Conditional** fields are declared like `data` in the following example:
```
public type Foo = unit {
    cond: uint8;
    len: uint8;
    data: bytes &size=self.len if(self.cond > 0);
};
```
---
* **Bitfields** parse an integer value of 8, 16, 32 or 64 bits (only these are supported), and then make selected smaller bit ranges within that value available individually through dedicated identifiers. For example, the following unit parses 4 bytes as an uint32 and then makes the value of bit 0 available as f.x1, bits 1 to 2 as f.x2, and bits 3 to 4 as f.x3, respectively:
```
public type Foo = unit {
    f: bitfield(32) {
        x1: 0;
        x2: 1..2;
        x3: 3..4;
    };
};
```
Whenever there is a fixed-length field whose length is not byte-aligned (i.e., less than 8 bits or say 12 bits), you should use a bitfield to join it with following fields until reaching a size of 8/16/32/64 instead of using an integer type.
It is important to use the correct bit order parsing, this can be modified via an attribute attached to the field. When parsing a `bitfield(16)` in network byte order and with bit order `spicy::BitOrder::LSB0` (default value of `&bit-order`), bits are numbered 0 to 15 from right to left.
```
MSB                           LSB
      <--   1         <--       0
6 5 4 3 2 1 0 9 8 7 6 5 4 3 2 1 0
+---------------+---------------+
|               |               |
+-------------------------------+
```
However, you may need to use the inverse order, where the most significant bit is numbered 0 on the left with higher bit numbers representing less significant bits to the right. Here is an example
```
MSB                           LSB
0       -->         1    -->
0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6
+-+-+-+-+-------+-+-------------+
|F|R|R|R| opcode|M| Payload len |
|I|S|S|S|  (4)  |A|     (7)     |
|N|V|V|V|       |S|             |
| |1|2|3|       |K|             |
+-+-+-+-+-------+-+-------------+
```
To parse a bitfield with this order, do like the following example
```
import spicy;

public type Header= unit {
    : bitfield(32) {
        fin: 0;
        rsv: 1..3;
        opcode: 4..7;
        mask: 8;
        payload_len: 9..15;
    } &bit-order=spicy::BitOrder::MSB0;
};
```
Without the `&bit-order` attribute, the final parsed structure would have been the reversed bitfield.
```
MSB                           LSB
0       -->         1    -->
0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6
+-------------+-+-------+-+-+-+-+
| Payload len |M| opcode|R|R|R|F|
|     (7)     |A|  (4)  |S|S|S|I|
|             |S|       |V|V|V|N|
|             |K|       |3|2|1| |
+-------------+-+-------+-+-+-+-+
```

Fields inside the bitfield should have a `&convert` attribute in case these fields correspond to an `enum` type. For example
```
type Flag = enum {
  On,
  Off,
}

type Bar = unit {
    f: bitfield(8) {
        flag: 0..1 &convert=Flag($$);
    };
};
```

---
* **Enum** types associate labels with numerical values. Only unsigned integer values can be used in enum declarations. Each enum type comes with an implicitly defined `Undef` label with a value distinct from all other ones. When coerced into a boolean, an enum will be true iff it’s not `Undef`.
```
type Code = enum {
  A = 1,
  B = 2,
  C = 5,
};

public type Msg = unit {
  code: uint8 &convert=Code($$);
  switch (self.code) {
    Code::A,
    Code::B -> payload: bytes &eod
    Code::C -> error: uint8
    * -> : void;
  };
};
```
