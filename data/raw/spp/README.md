# Space Packet Protocol Test Seeds

To generate parse trees for any seed, use the following command:
`./build/posix/ud3tndecode -s <filename>.bin`

---

### Valid Packets

**spp_1.bin** (Source: uD3TN default seed)
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
