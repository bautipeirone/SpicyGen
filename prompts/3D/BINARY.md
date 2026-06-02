# Grammar specification language: EverParse 3D
**Only use the 3D syntax explained in this document. It is STRICTLY prohibited to use syntax not declared here.**
**The output MUST be one valid `.3d` file. Do not output explanations, markdown, comments outside the `.3d` file, or code in any other language.**
**There must be exactly one `entrypoint` declaration in the entire grammar. The entrypoint type name must be capitalized and should be a single word such as `Packet`, `Message`, `Frame`, or `Segment`.**

## General guidelines
1. 3D is a binary data description language used to define validators for structured byte formats. A 3D grammar should describe the exact layout of a protocol message, including field order, field sizes, byte order, constraints, variable-length data, optional fields, and tag-dependent alternatives.
2. Define one main `entrypoint typedef struct` for the top-level protocol data unit. Define additional helper structs, casetypes, enums, and constants globally as needed, but do not mark them as `entrypoint`.
3. Prefer one 3D type per meaningful protocol structure: headers, fixed subheaders, variable payloads, tagged alternatives, options, extension fields, etc.
4. Every field mentioned in the protocol specification should be represented as its own field whenever possible. Do not merge multiple semantically distinct fields into one integer unless they are later exposed individually as bitfields.
5. The grammar must reflect the wire format, not a convenient in-memory representation. 3D does not add implicit padding or alignment by default. If padding exists in the protocol, represent it explicitly as a field.
6. Fields are parsed in declaration order. Constraints on a field may refer to parameters and to previous fields in the same struct, but not to fields that appear later.
7. Do not repeat fields from a parent structure in substructures. Pass previously parsed values as parameters to helper types when those values determine the format of later fields.
8. Use parameterized types when a substructure depends on a length, tag, version, flag, or other value parsed earlier.
9. Use `casetype` or an inline `switch` for alternatives whose layout depends on a tag, flag, type code, length code, or presence indicator.
10. Use `unit` for an empty structure or empty alternative.
11. Do not invent parsing functions, hooks, casts, string operations, regular expressions, or procedural code unless they are explicitly listed in this document.
12. Avoid field names that collide with language keywords or common generated-code names. Prefer descriptive names such as `payload_length`, `header_length`, `sequence_number`, `checksum`, `reserved`, and `padding`.
13. In 3D, fields are referenced by name directly. Do not use dot notation such as `field.Type`, `header.length`, or `self.length`; that is not valid 3D.
14. Do not use ternary expressions such as `cond ? a : b`.
15. Use comments sparingly. Line comments use `//`. Block comments use `/* ... */` and do not nest.
16. Declare and define types before using them.

## Basic file structure
A typical 3D file has this shape:
```
#define CONSTANT_NAME 1

typedef struct _Helper {
  UINT8 field1;
  UINT16BE field2;
} Helper;

casetype _Payload(UINT8 tag) {
  switch(tag) {
    case 1:
      UINT8 value1;
    case 2:
      UINT16BE value2;
    default:
      unit Unknown;
  }
} Payload;

entrypoint typedef struct _Packet {
  UINT8 tag;
  UINT16BE length;
  Payload(tag) payload[:byte-size length];
} Packet;
```

## Base types
Use only the supported 3D integer types and constructs.

### Unsigned little-endian integer types
```
UINT8
UINT16
UINT32
UINT64
```
These represent unsigned little-endian integers, except `UINT8`, where endianness is irrelevant for byte-sized integer values.

### Unsigned big-endian integer types
```
UINT8BE
UINT16BE
UINT32BE
UINT64BE
```
Use the big-endian variants for network byte order fields. Most Internet and space/network protocol specifications use big-endian multi-byte integers unless the specification explicitly says otherwise.

### Empty type
Use `unit` for empty alternatives or empty structures:
```
unit Empty;
```

Example:
```
casetype _OptionalData(UINT8 present) {
  switch(present) {
    case 0:
      unit Empty;
    case 1:
      UINT16BE value;
  }
} OptionalData;
```

## Constants
Use `#define` for numeric constants:
```
#define TYPE_DATA 1
#define TYPE_ACK  2
#define TYPE_ERR  3
```
Constants are useful for `switch` cases and constraints.

## Enumerations
Use enums to define a constrained set of integer values:
```
UINT8 enum MessageKind {
  KindData = 1,
  KindAck = 2,
  KindError = 3
};
```
The first enum label must have an explicit value.

Example usage:
```
entrypoint typedef struct _Message {
  MessageKind kind;
  UINT16BE length;
} Message;
```
Important limitation: do not use enum-typed fields inside constraints as this causes reader or double-fetch errors. A robust workaround is to store the field as the underlying integer type and add an explicit constraint:
```
#define KIND_DATA  1
#define KIND_ACK   2
#define KIND_ERROR 3
entrypoint typedef struct _Message {
  UINT8 kind {
    kind == KIND_DATA ||
    kind == KIND_ACK ||
    kind == KIND_ERROR
  };
  UINT16BE length;
} Message;
```

## Structs
Use `typedef struct` for fixed-order protocol structures.
```
typedef struct _Header {
  UINT8 version;
  UINT8 flags;
  UINT16BE length;
} Header;
```

If later fields need values from a nested header, prefer flattening the needed controlling fields into the parent or passing the controlling value explicitly through parameters.
```
typedef struct _Payload(UINT16BE length) {
  UINT8 data[:byte-size length];
} Payload;

entrypoint typedef struct _Packet {
  UINT8 version;
  UINT8 flags;
  UINT16BE payload_length;
  Payload(payload_length) payload;
} Packet;
```

## Constraints
A constraint is written after a field in braces:
```
entrypoint typedef struct _Smoker {
  UINT32 age { age >= 21 };
  UINT8 cigarettes_consumed;
} Smoker;
```

Constraints can use +, -, *, /, ==, !=, <=, <, >=, >, &&, ||, !. They may refer to parameters and previous fields:
```
entrypoint typedef struct _OrderedPair {
  UINT32 lesser;
  UINT32 greater { lesser <= greater };
} OrderedPair;
```

Use constraints to enforce protocol validity:
```
entrypoint typedef struct _Packet {
  UINT8 version { version == 1 };
  UINT8 header_length { header_length >= 4 };
  UINT16BE payload_length { payload_length <= 1024 };
} Packet;
```

Do not put constraints on non-scalar fields such as structs, arrays, or casetype payloads. Put constraints on the scalar fields that determine their layout.

## Writing constraints that F* can prove
3D verifies arithmetic safety using machine integers, not mathematical integers. Therefore, every arithmetic expression must be proven not to overflow or underflow before it is evaluated.
Avoid writing constraints that require computing a potentially overflowing expression:
```
// Bad: left + right may overflow before comparison.
UINT32 right { left + right <= 42 };
```
Rewrite the constraint so that the arithmetic is guarded:
```
// Good: no overflow, and 42 - left is safe because left <= 42 is known first.
UINT32 right { left <= 42 && right <= 42 - left };
```
The order of conditions in `&&` matters. Put the safety guard first, then the expression that depends on it.

### Common arithmetic-proof patterns
#### Bounded addition
Instead of:
```
UINT32 b { a + b <= limit };
```
Use:
```
UINT32 b { a <= limit && b <= limit - a };
```

#### Safe subtraction
Instead of:
```
UINT16BE payload_length;
UINT8 data[:byte-size payload_length - 4];
```
Use a `where` clause or a prior constraint that proves the subtraction safe:
```
typedef struct _Payload(UINT16BE payload_length)
where payload_length >= 4
{
  UINT8 data[:byte-size payload_length - 4];
} Payload;
```

#### Safe division
Do not divide unless the divisor is known to be nonzero first:
```
UINT32 divisor { divisor != 0 };
UINT32 value { value / divisor <= 10 };
```

#### Safe multiplication
Avoid multiplication in constraints unless the operands are tightly bounded first:
```
UINT32 count { count <= 64 };
UINT32 elem_size { elem_size <= 16 };
UINT32 total { total == count * elem_size };
```
If F* cannot prove the multiplication safe, rewrite the protocol model to use an explicit byte length field or add stronger bounds before the multiplication is used.

#### Length-minus-header pattern
For protocols with a total length that includes a fixed header:
```
typedef struct _Body(UINT16BE total_length)
where total_length >= 8
{
  UINT8 bytes[:byte-size total_length - 8];
} Body;

entrypoint typedef struct _Packet {
  UINT16BE total_length { total_length >= 8 };
  Body(total_length) body;
} Packet;
```

#### Prefer `where` for preconditions on parameters
If a parameter must satisfy a condition before it is used in field sizes, put that condition in a `where` clause:
```
typedef struct _Data(UINT16BE length)
where length > 0
{
  UINT8 data[:byte-size length - 1];
} Data;
```
This helps F* prove that expressions such as `length - 1` are safe.

## Bitfields
Use bitfields whenever the protocol contains fields that are not byte-aligned, such as 1-bit flags, 3-bit versions, 4-bit opcodes, 12-bit lengths, etc.
Bitfields are written like this:
```
typedef struct _Flags {
  UINT16BE version : 4;
  UINT16BE kind    : 4;
  UINT16BE length  : 8;
} Flags;
```
A bitfield field has:
```
BASE_TYPE field_name : WIDTH;
```
A bitfield may also have a constraint:
```
typedef struct _HeaderBits {
  UINT16BE version : 4 { version == 1 };
  UINT16BE flags   : 4;
  UINT16BE length  : 8 { length <= 128 };
} HeaderBits;
```

### Bitfield base types
Any base integer type can be used for bitfields. Use `UINT16BE`, `UINT32BE`, or `UINT64BE` for network-order protocol diagrams where the first drawn bit is the most significant bit. Use `UINT8BE` for an 8-bit MSB-first bitfield.

### Critical bit-order rule
Bitfield order is one of the most common sources of wrong 3D grammars.
For little-endian bitfield base types 3D uses least-significant-bit-first packing. The first declared bitfield occupies the least significant bits of the storage unit.
For big-endian bitfield base types 3D uses most-significant-bit-first packing. The first declared bitfield occupies the most significant bits of the storage unit.
Most RFC-style packet diagrams are drawn from most significant bit to least significant bit. For those diagrams, use big-endian bitfield base types and declare fields in the same left-to-right order as the diagram.
Example RFC-style 16-bit header:
```text
0                   1
0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|Ver|  Type |R|     Length      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 4     4    1        7
```
Correct 3D:
```
typedef struct _HeaderBits {
  UINT16BE version : 4;
  UINT16BE message_type : 4;
  UINT16BE reserved : 1;
  UINT16BE length : 7;
} HeaderBits;
```
Do not use `UINT16` for this diagram unless the specification explicitly uses least-significant-bit-first packing.

### Bitfield storage-unit boundaries
A bitfield must fit within the bounds of its underlying storage unit. 3D follows C-like packing rules: when a field cannot fit in the remaining bits of the current storage unit, it starts in a new storage unit and unused bits may be inserted.
Example:
```
typedef struct _BadlyPacked {
  UINT16BE x : 6;
  UINT16BE y : 12;
  UINT8BE z;
} BadlyPacked;
```
Although `x`, `y`, and `z` add up to 26 bits, `y` cannot be split freely across storage-unit boundaries if it does not fit in the remaining part of the current `UINT16BE`. This may create unused bits. Be careful to group bitfields into 8-, 16-, 32-, or 64-bit storage units exactly as the protocol defines them.
When a protocol has a continuous 32-bit bit layout, prefer using `UINT32BE` bitfields that add up to 32 bits:
```
typedef struct _Word {
  UINT32BE field_a : 3;
  UINT32BE field_b : 13;
  UINT32BE field_c : 16;
} Word;
```
When a protocol has separate byte-sized bit layouts, use separate `UINT8BE` groups:
```
typedef struct _TwoFlagBytes {
  UINT8BE flag_a : 1;
  UINT8BE flag_b : 1;
  UINT8BE reserved1 : 6;
  UINT8BE code : 4;
  UINT8BE reserved2 : 4;
} TwoFlagBytes;
```

### Bitfield constraints and arithmetic
Bitfield values can be used in later constraints. 3D knows their bounds from their widths. For example, a 4-bit field is known to be between 0 and 15.
```
typedef struct _Header {
  UINT16BE length_code : 4;
  UINT16BE flags : 4;
  UINT16BE reserved : 8;
  UINT8 payload[:byte-size length_code];
} Header;
```
Still apply normal F* arithmetic safety rules when doing arithmetic with bitfield values:
```
typedef struct _Payload(UINT16BE total_length)
where total_length >= 4
{
  UINT8 data[:byte-size total_length - 4];
} Payload;
```

## Parameterized data types
A struct or casetype can take parameters:
```
typedef struct _Data(UINT16BE length)
where length > 0
{
  UINT8 data[:byte-size length];
} Data;
```
Instantiate it with a previous field:
```
entrypoint typedef struct _Packet {
  UINT16BE length { length > 0 };
  Data(length) payload;
} Packet;
```
Use parameters instead of trying to access nested fields with dot notation.
Good:
```
typedef struct _Body(UINT16BE payload_length) {
  UINT8 bytes[:byte-size payload_length];
} Body;
entrypoint typedef struct _Packet {
  UINT16BE payload_length;
  Body(payload_length) body;
} Packet;
```
Bad:
```
// Invalid 3D style: do not use header.payload_length.
Body(header.payload_length) body;
```

## Tagged unions and `casetype`
Use `casetype` when a field’s layout depends on a tag or selector.
```
#define TYPE_U8  1
#define TYPE_U16 2
#define TYPE_U32 3
casetype _Payload(UINT8 tag) {
  switch(tag) {
    case TYPE_U8:
      UINT8 value8;
    case TYPE_U16:
      UINT16BE value16;
    case TYPE_U32:
      UINT32BE value32;
    default:
      unit Unknown;
  }
} Payload;

entrypoint typedef struct _Message {
  UINT8 tag;
  Payload(tag) payload;
} Message;
```
The value inside `switch(...)` must be a simple name in scope, such as `tag`, `kind`, or `message_type`. Do not write expressions such as `switch(tag + 1)`.
A `casetype` defines a value-dependent untagged union. 3D does not force all cases to have the same size and does not add padding. If the protocol requires padding or fixed-size alternatives, model that padding explicitly. If some branch needs to parse more than one field use a `struct`.

## Inline switch fields
A struct may also contain an inline switch:
```
typedef struct _Message {
  UINT8 tag;
  switch(tag) {
    case 1:
      UINT8 small_value;
    case 2:
      UINT16BE large_value;
    case 3: struct {
      UINT16BE value_1;
      UINT16BE value_2;
    } Pair;
    default:
      unit unknown;
  } payload;
} Message;
```
Use either a reusable `casetype` or an inline switch. Prefer `casetype` when the same alternative layout is reused or when it improves clarity.

## Optional fields
Model optional fields with a `casetype` whose empty case is `unit`.
```
casetype _AuthSection(UINT8 present) {
  switch(present) {
    case 0:
      unit Empty;
    case 1:
      struct {
        UINT8 auth_type;
        UINT16BE auth_length;
        UINT8 auth_payload[:byte-size auth_length];
      } auth_section;
  }
} AuthSection;

entrypoint typedef struct _Packet {
  UINT8 auth_present;
  AuthSection(auth_present) auth;
} Packet;
```
If the optional field is controlled by a flag bit, parse the flag in a bitfield first and pass it to the optional casetype:
```
casetype _Trailer(UINT8BE has_trailer) {
  switch(has_trailer) {
    case 0:
      unit Empty;
    case 1:
      UINT32BE trailer_crc;
  }
} Trailer;

entrypoint typedef struct _Frame {
  UINT8BE has_trailer : 1;
  UINT8BE reserved : 7;
  UINT16BE payload_length;
  UINT8 payload[:byte-size payload_length];
  Trailer(has_trailer) trailer;
} Frame;
```

## Arrays
Arrays in 3D are sized in bytes, not in element count, except when the element size is exactly one byte.
Use:
```
T field[:byte-size n]
```
This means that `field` contains an array of `T` elements whose total byte size is `n`.
Other possibility is to use `data[:consume-all]` to consume all remaining data in the stream. Only use it when its absolutely necessary.

## Variable-length payloads
Use a previous length field to size a payload:
```
entrypoint typedef struct _Packet {
  UINT16BE length;
  UINT8 payload[:byte-size length];
} Packet;
```
If some arithmetic needs to be done on the length, first prove it doesn't underflow:
```
typedef struct _Sub(UINT16BE total_length)
where total_length >= N
{
  UINT8 payload[:byte-size total_length - N];
} Sub;
```
If you need to add to the length, you can cast the value inside the expression to a bigger type so it doesn't overflow
```
typedef struct _Add(UINT16BE total_length)
{
  UINT8 payload[:byte-size ((UINT32BE) total_length) + N];
} Add;
```

## Padding and reserved fields
3D does not insert implicit padding. If the protocol has padding, reserved bytes, or reserved bits, represent them explicitly.
Reserved byte:
```
UINT8 reserved;
```
Reserved bits:
```
typedef struct _Flags {
  UINT8BE flag_a : 1;
  UINT8BE flag_b : 1;
  UINT8BE reserved : 6 { reserved == 0 };
} Flags;
```

## Checksums, CRCs, cryptographic fields, and semantic validation
3D is primarily for structural validation and local field constraints. Do not invent checksum, CRC, cryptographic, compression, or decompression logic.
Represent checksum fields structurally:
```
UINT32BE checksum;
```

## Naming conventions
1. Entrypoint type: capitalized single word, e.g. `Packet`.
2. Helper type aliases: capitalized or descriptive, e.g. `Header`, `Payload`, `FrameHeader`.
3. Struct tags: prefix with underscore, e.g. `_Packet`, `_Header`.
4. Field names: lowercase with underscores, e.g. `payload_length`, `sequence_number`.
5. Constants: uppercase with underscores, e.g. `TYPE_DATA`.
6. Do not use `type` as a field name.
7. Avoid dot notation entirely.

## Common protocol modeling patterns
### Fixed header plus variable payload
```
typedef struct _Payload(UINT16BE length) {
  UINT8 bytes[:byte-size length];
} Payload;

entrypoint typedef struct _Packet {
  UINT8 version;
  UINT8 message_type;
  UINT16BE payload_length;
  Payload(payload_length) payload;
} Packet;
```

### Tag-length-value
```
#define TAG_U8  1
#define TAG_U16 2

casetype _Value(UINT8 tag, UINT16BE length) {
  switch(tag) {
    case TAG_U8:
      UINT8 value[:byte-size length];
    case TAG_U16:
      UINT16BE value[:byte-size length];
    default:
      UINT8 raw[:byte-size length];
  }
} Value;

entrypoint typedef struct _TLV {
  UINT8 tag;
  UINT16BE length;
  Value(tag, length) value;
} TLV;
```

### Flags controlling optional sections
```
casetype _Extension(UINT8BE has_extension, UINT16BE extension_length) {
  switch(has_extension) {
    case 0:
      unit Empty;
    case 1:
      UINT8 extension_data[:byte-size extension_length];
  }
} Extension;
entrypoint typedef struct _Packet {
  UINT8BE has_extension : 1;
  UINT8BE reserved : 7;
  UINT16BE extension_length;
  Extension(has_extension, extension_length) extension;
} Packet;
```
