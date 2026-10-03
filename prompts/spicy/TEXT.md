# Grammar specification language
**Only use the tools explained in this document. It is STRICTLY prohibited to use syntax not declared here.**
**The name of the module MUST be <PROTOCOL_NAME>_<LLM_NAME>.**

## General guidelines
1. Generally, you define parsers through types called “units” that describe the syntax of a protocol. A set of units forms a grammar. In practice, units typically correspond pretty directly to protocol data units (PDUs) as protocol specifications tend to define them.
2. One will typically create a unit type for each of the main data units that a protocol defines.
3. There must be only one `public type` declaration in the entire grammar, which is the entry point for the main parser of the protocol.
4. Subunits don't have to repeat fields declared in the parent units.
5. Inside a unit, `self` references to the current unit, and its fields can be accessed via `self.field`, and `$$` references to the last read input, this can be used to perform conversions or conditional parsing.
6. Every field mentioned in the specification should be accessible by itself, not as a combination of fields like merging them into an integer type,
so parsing a whole packet like `data: bytes &eod` is not allowed.

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
* Boolean values are `True` and `False`.
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
Note that `&eod` SHOULD NOT be used with `&until`, since this will be omitted.
* Use `|bytes|` to calculate the length of a byte array.

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
* Use `|vector|` to calculate the length of the vector.

### Parser state, hooks, and structural constraints
Use unit variables when the parser must remember information that is not a direct input field, such as whether a body is present, a derived body length, a selected delivery mode, or a value extracted from earlier headers. Variables are declared inside a unit with `var`:
```
type Message = unit {
    headers: (Header(self))[];
    : CRLF;
    body: bytes &size=self.content_length if ( self.has_body );

    var has_body: bool = False;
    var content_length: uint64 = 0;
};
```
Use `&optional` on a variable when the value may not have been seen yet. Check for presence with `self?.field` before reading it:
```
type Message = unit {
    headers: (Header(self))[];
    : CRLF;
    body: bytes &size=self.content_length if ( self?.content_length );

    var content_length: uint64 &optional;
};
```
Units can receive parameters. Use `inout` when a subunit must update state in its parent while it parses. This is useful for structural constraints like "if this header appears, parse the following body as length-delimited":
```
type Header = unit(inout msg: Message) {
    name: HeaderName;
    : b":";
    value: bytes &until=b"\r\n";

    on value {
        local name = self.name.upper();

        if ( name == b"CONTENT-LENGTH" ) {
            msg.content_length = self.value.to_uint();
            msg.has_body = True;
        }
    }
};
```
Use `on FIELD { ... }` hooks to react once a field has been parsed, and `on %init { ... }` to initialize state from unit parameters before fields parse. Use `on FIELD` after repeated fields or delimiter fields when a decision depends on all data parsed so far.
```
type Message = unit(body_default: bool) {
    headers: (Header(self))[];
    end_of_headers: CRLF;
    body: bytes &size=self.content_length if ( self.has_body );

    var has_body: bool;
    var content_length: uint64 = 0;

    on %init {
        self.has_body = body_default;
    }

    on end_of_headers {
        if ( self.content_length > 0 )
            self.has_body = True;
    }
};
```
For structural constraints, prefer deriving explicit parser state and then using that state in `if`, `&size`, `&until`, `switch`, or `&requires`. This is better than parsing everything as opaque bytes. Examples include checking mandatory magic values, validating version ranges, selecting a body format from a header, or parsing trailer headers only when a chunked transfer encoding was seen.

When a condition is invalid for the protocol and the packet must be rejected, use `throw "error message";` from a hook or a switch branch. Use `if` only for optional fields that are legitimately absent; do not use `if` to silently accept malformed packets. Use `&requires` for simple field-local constraints, and use `throw` when the condition depends on derived state or needs custom logic.
```
type Message = unit {
    version: uint8 &requires=($$ = 1);
    flags: uint8;

    on flags {
        if ( (self.flags & 0xf0) != 0 )
            throw "reserved flag bits are set";
    }
};
```

### Error recovery and synchronization
When the grammar may run inside a Zeek analyzer, consider whether parsing can resume at a later message or record after malformed input. Add `&synchronize` only when the protocol provides a reliable, protocol-defined boundary that Spicy can search for. Good synchronization targets begin with a mandatory literal or regular expression, such as a fixed command set (`/GET|POST/`), a record marker, or a line delimiter. The target should be distinctive enough that matching the same bytes inside payload data is unlikely.

Place `&synchronize` on the field at which parsing can safely resume. For repeated units, place it on the vector element type:
```
type Request = unit {
    method: /GET|POST/;
    : WhiteSpace;
    target: Token;
    newline: CRLF;

    on newline { confirm; }
};

public type Session = unit {
    requests: (Request &synchronize)[] &eod;
};
```
This works because every `Request` begins with a mandatory regular expression. A normal unit field may be marked in the same way: `next: Request &synchronize;`.

To make the recovery boundary explicit, or when Spicy cannot derive it from the target field, declare a synchronization property inside the target unit. `%synchronize-at` leaves the matched literal in the input, so the resumed unit must begin by consuming it. `%synchronize-after` consumes the matched delimiter first, so the unit must describe the content immediately after that delimiter:
```
type Command = unit {
    %synchronize-at = /GET|POST/;
    method: /GET|POST/;
    : WhiteSpace;
    target: Token;
    newline: CRLF;

    on newline { confirm; }
};

type LineAfterError = unit {
    %synchronize-after = /\r?\n/;
    content: RestOfLine;
    newline: CRLF;

    on newline { confirm; }
};
```
These properties override any synchronization point Spicy could infer from the unit and have an effect only when a field of that unit type is marked `&synchronize`. Do not parse a `%synchronize-after` delimiter again at the start of the recovered unit because synchronization has already consumed it.

After finding a marker, Spicy parses in trial mode. The recovered unit must execute `confirm;` from a hook after enough subsequent structure has parsed to establish that the marker is genuine; reaching the end of the grammar without confirming is a fatal error. Calling `confirm` during normal parsing has no effect. A highly distinctive marker may be confirmed immediately with `on %synced { confirm; }`, but prefer a later field hook when the marker might also occur in payload data.

Do not add synchronization merely because the parser is intended for Zeek. Omit it when there is no unambiguous literal or regex boundary, when recovery would resume in the middle of a dependent message, or when the candidate marker can commonly occur in payload data. Never invent a magic value or delimiter that the protocol specification does not define.

## Line Based Parsing
This section describes common and reusable patterns for parsing line-based protocols. 
These are common regex definitions to use.
```
const Token      = /[^ \t\r\n]+/;
const HeaderName  = /[^:\r\n]+/;
const WhiteSpace = /[ \t]+/;
const OptionalWhiteSpace = /[ \t]*/;
const CRLF    = /\r?\n/;
const RestOfLine = /[^\r\n]*/;
const Integer = /[0-9]+/;
const HexInteger = /[0-9a-fA-F]+/;
```
Their usage is highly encouraged to achieve correct parsing. When reading a value until the end of line, using the `&until` attribute is prefered.
This is an example of how to parse the `key: value` header style that is very common among text protocols.
```
module Test;

import spicy;

const Token      = /[^ \t\r\n]+/;
const HeaderName  = /[^:\r\n]+/;
const WhiteSpace = /[ \t]+/;
const CRLF    = /\r?\n/;

public type KeyValue = unit {
  key: HeaderName;
  : b":";
  : WhiteSpace;
  value: bytes &until=b"\r\n";
};
```
For HTTP-like protocols, use a parent message unit to collect headers and let each header update the parent state. Then parse the body according to that state after the blank line:
```
type DeliveryMode = enum { EndOfData, Length, Chunked };

type Message = unit(body_default: bool) {
    headers: (Header(self))[];
    end_of_headers: CRLF;

    switch ( self.delivery_mode ) {
      DeliveryMode::Length  -> body: bytes &size=self.content_length if ( self.has_body );
      DeliveryMode::Chunked -> chunks: Chunk[] &until=($$.length == 0) if ( self.has_body );
      DeliveryMode::EndOfData -> : void;
      * -> : void { throw "unsupported delivery mode"; }
    };

    var has_body: bool;
    var content_length: uint64 = 0;
    var delivery_mode: DeliveryMode = DeliveryMode::EndOfData;
    var content_type: bytes &optional;
    var transfer_encoding: bytes &optional;

    on %init {
        self.has_body = body_default;
    }

    on end_of_headers {
        if ( self.content_length > 0 )
            self.delivery_mode = DeliveryMode::Length;

        if ( self?.transfer_encoding && self.transfer_encoding != b"chunked" )
            throw "unsupported transfer encoding";
    }
};

type Header = unit(inout msg: Message) {
    name: HeaderName;
    : /:[\t ]*/;
    content: RestOfLine;
    : CRLF;

    on content {
        local name = self.name.upper();

        if ( name == b"CONTENT-LENGTH" ) {
            msg.content_length = self.content.to_uint();
            msg.has_body = True;
        }

        if ( name == b"TRANSFER-ENCODING" && self.content == b"chunked" ) {
            msg.transfer_encoding = self.content;
            msg.delivery_mode = DeliveryMode::Chunked;
            msg.has_body = True;
        }

        if ( name == b"TRANSFER-ENCODING" && self.content != b"chunked" )
            msg.transfer_encoding = self.content;

        if ( name == b"CONTENT-TYPE" )
            msg.content_type = self.content;
    }
};

type Chunk = unit {
    length: HexInteger &convert=$$.to_uint(16);
    : CRLF;
    data: bytes &size=self.length if ( self.length > 0 );
    : CRLF if ( self.length > 0 );
};
```
This pattern also works for trailers: parse trailer headers with the same `Header(msg)` unit when they should update the same message state.

This is the desired pattern for branching between requests and replies. Note that for this to work, methods MUST be listed explictly, so that the look ahead parsing works correctly, parsing with Token is wrong here.
```
type RequestLine = unit {
    method: /GET|POST|DELETE/;
    :        WhiteSpace;
    uri:     Token;
    :        WhiteSpace;
    version: Version;
    :        CRLF;
};

# Code for parsing replies and headers...

public type Message = unit {
    switch {
      -> request: RequestLine;
      -> reply:   ReplyLine;
    };
    headers: KeyValue[];
    : CRLF;
};
```
Use the following pattern when you have to parse text fields that may appear or not, and that are identified by how they start. The patterns MUST NOT be ambiguous among the branches to match.
```
const Argument = /[a-zA-Z]*/;

type Optional = unit {
  : b"-";
  flag: Argument;
};

public type Main = unit {
    switch {
      -> { opt: Optional; arg: Argument; }
      -> { arg: Argument; }      
    };
};
```
The following is an incorrect way of doing it because both branches match for inputs that start with "-".
```
public type Wrong = unit {
  switch {
    -> { opt: Optional; arg: Token; }
    -> { arg: Token; }
  };
};
```
Avoid this by making the branches mutually exclusive on what they match.

## Examples
* **switch** constructs are supported as a way to branch into one of several parsing alternatives.

Every `switch` you write MUST include an explicit default branch (`* ->`) to handle unknown or unexpected cases instead of relying on an unmatched-switch runtime exception. If the protocol provides a sensible way to parse an unknown branch, use it. For example, when a previously parsed length field defines the payload size and the protocol permits unknown types, preserve the payload as `unknown_payload: bytes &size=self.length;` in the default branch. A protocol-defined line or record delimiter can also provide a safe boundary. Respect that boundary so parsing can continue with the next field or message; do not consume the rest of the stream with `&eod` unless the protocol defines that boundary.

If there is no sensible, protocol-defined way to parse the branch, or the value is forbidden by the protocol, put a `throw` statement with a descriptive message inside the default branch. In a parsing switch, attach it to a `void` field hook:
```
* -> : void {
    throw "unsupported or unexpected message type";
}
```
Do not use an empty default (`* -> : void;`) to silently accept an unknown value. A legitimate case with no payload should have its own explicit branch. For look-ahead switches, a bare `void` fallback cannot supply a look-ahead symbol, and a catch-all regex may overlap existing alternatives. When needed, factor out and parse the common discriminator first, then use a value-based switch with an explicit default.

```
public type Foo = unit {
    x: bytes &size=1;
    switch ( self.x ) {
        b"A" -> a8: int8;
        b"B" -> a16: int16;
        b"C" -> : void;
        * -> {
          : void {
              throw "unsupported field discriminator";
          }
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
If you get any errors like `production switch is ambiguous for look-ahead symbol(s)`, try to factor out the common
prefix out of its branches. You can also use hooks to enforce constraints instead of validating them from the switch
branches automatically.

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
    * -> : void { throw "unsupported code"; }
  };
};
```
