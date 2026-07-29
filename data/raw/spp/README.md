# Space Packet Protocol Test Seeds

To generate parse trees for any seed, use the following command:
`./build/posix/ud3tndecode -s <filename>.bin`

---

### Valid Packets

**spp_01.bin** (Source: uD3TN default seed)
* **Hex:** `0801c000007e1c7b005b8e9f890700018201722f2f622e64746e2f62756e646c6573696e6b820178302f2f756433746e2e64746e2f33643335396131642d373730612d343161662d623439662d663561366664323963653239820100821b000000abe2d4d2b0011a05265c0042801685010100004d48656c6c6f2c20776f726c6421ffd623`
* **Parse Tree:**
  - apid: 1
  - segment number: 0
  - dtn timestamp: 0
  - dtn counter: 0
  - segment status: 0003
  - data length: 127
  - has secondary header: 1
  - is request: 0

**spp_02.bin** (Source: Peach Pit generator)
* **Hex:** `022b00000003abcdef01`
* **Parse Tree:**
  - apid: 555
  - segment number: 0
  - dtn timestamp: 0
  - dtn counter: 0
  - segment status: 0000
  - data length: 4
  - has secondary header: 0
  - is request: 0

**spp_03.bin** (Source: Custom valid input)
* **Hex:** `08080001000000`
* **Parse Tree:**
  - apid: 8
  - segment number: 1
  - dtn timestamp: 0
  - dtn counter: 0
  - segment status: 0000
  - data length: 1
  - has secondary header: 1
  - is request: 0

| File | Hex | Primary-header interpretation | Purpose |
|---|---|---|---|
| `spp_04.bin` | `0801c000007e1c7b005b8e9f890700018201722f2f622e64746e2f62756e646c6573696e6b820178302f2f756433746e2e64746e2f33643335396131642d373730612d343161662d623439662d663561366664323963653239820100821b000000abe2d4d2b0011a05265c0042801685010100004d48656c6c6f2c20776f726c6421ffd623` | PVN=0, type=0, SHF=1, APID=1, flags=11, count/name=0, C=126, data=127 B | Original uD3TN seed; telemetry, secondary header present, APID 1, unsegmented, 127 data octets. |
| `spp_05.bin` | `022b00000003abcdef01` | PVN=0, type=0, SHF=0, APID=555, flags=00, count/name=0, C=3, data=4 B | Original Peach seed; telemetry, no secondary header, APID 555, continuation segment, 4 data octets. |
| `spp_06.bin` | `08080001000000` | PVN=0, type=0, SHF=1, APID=8, flags=00, count/name=1, C=0, data=1 B | Original custom seed; telemetry, secondary header present, APID 8, continuation segment, minimum one-octet data field. |
| `spp_07.bin` | `0000c000000000` | PVN=0, type=0, SHF=0, APID=0, flags=11, count/name=0, C=0, data=1 B | Minimum-size packet; telemetry, APID 0, no secondary header, unsegmented, one data octet. |
| `spp_08.bin` | `17feffff0002deadbe` | PVN=0, type=1, SHF=0, APID=2046, flags=11, count/name=16383, C=2, data=3 B | Telecommand, APID 2046 boundary, no secondary header, unsegmented, maximum 14-bit packet name/count. |
| `spp_09.bin` | `082a407b000700010203aabbccdd` | PVN=0, type=0, SHF=1, APID=42, flags=01, count/name=123, C=7, data=8 B | Telemetry with a managed four-octet secondary header, APID 42, first segment, nonzero sequence count. |
| `spp_10.bin` | `1c00c001000f000102030405060708090a0b0c0d0e0f` | PVN=0, type=1, SHF=1, APID=1024, flags=11, count/name=1, C=15, data=16 B | Telecommand with secondary header, APID 1024, unsegmented, 16 data octets. |
| `spp_11.bin` | `0064400a000346495253` | PVN=0, type=0, SHF=0, APID=100, flags=01, count/name=10, C=3, data=4 B | First packet of a coherent three-packet segmented user-data set, APID 100, count 10. |
| `spp_12.bin` | `0064000b0003434f4e54` | PVN=0, type=0, SHF=0, APID=100, flags=00, count/name=11, C=3, data=4 B | Continuation packet of the segmented set, APID 100, count 11. |
| `spp_13.bin` | `0064800c00034c415354` | PVN=0, type=0, SHF=0, APID=100, flags=10, count/name=12, C=3, data=4 B | Last packet of the segmented set, APID 100, count 12. |

---

### Invalid Packets

**invalid_spp_01.bin** (Source: Peach fuzzer)
* **Hex:** `c22b00000003abcdef01`
* **Error:** Fuzzed value; generic parser reset/failure.

**invalid_spp_02.bin** (Source: ESA SPP bug report)
* **Hex:** `20000000000064`
* **Error:** Invalid Packet Version Number (PVN).

**invalid_spp_03.bin** (Source: Custom malformed input)
* **Hex:** `070707070707070707070707070707070707070707070707`
* **Error:** Overreported length field leading to parser abort.

| File | Hex | Detectability | Violation |
|---|---|---|---|
| `invalid_spp_04.bin` | `20000000000064` | Generic structural | PVN is 001 instead of 000. Structural violation of 4.1.3.2.2; retained from the ESA bug report. |
| `invalid_spp_05.bin` | `0801c00000` | Generic structural | Only five octets, so the mandatory six-octet primary header is truncated. Structural violation of 4.1.2.1. |
| `invalid_spp_06.bin` | `0001c0000000` | Generic structural | Contains a complete primary header but no mandatory Packet Data Field. Structural violation of 4.1.2.1 and 4.1.4.1.2. |
| `invalid_spp_07.bin` | `070707070707070707070707070707070707070707070707` | Generic structural | Length field declares 1800 data octets, but only 18 are present. Structural underfill violating 4.1.3.5.2; retained from the original corpus. |
| `invalid_spp_08.bin` | `0003c0000000aabbcc` | Generic structural | Length field declares one data octet, but three are present when the file is treated as exactly one delimited packet. Structural overfill violating 4.1.3.5.2. |
| `invalid_spp_09.bin` | `0fffc000000000` | Generic idle rule | Uses reserved idle APID 2047 while setting Secondary Header Flag to 1. Idle packets must set it to 0 under 4.1.3.3.3.4. |

## Design notes

- `C` is the raw 16-bit Packet Data Length value. The actual Packet Data Field length must equal `C + 1` octets.
- The original three valid byte strings are retained, with `spp_1.bin` normalized to `spp_01.bin`.
- Two original invalid packets encoded the same PVN violation. Only the ESA bug-report example is retained. The original overreported-length example is also retained.
- `spp_11.bin` through `spp_13.bin` form one valid segmented sequence. The other valid packets exercise boundary values and independent header combinations.
- The standard defines secondary-header content through mission management. Arbitrary secondary-header content is therefore not labeled universally invalid.
- `invalid_spp_08.bin` assumes each `.bin` file contains exactly one delimited packet. In an undelimited byte stream, the trailing bytes could instead begin another packet.
