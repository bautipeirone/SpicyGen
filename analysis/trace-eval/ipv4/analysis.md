# IPv4 trace comparison

This analysis uses the existing [summary.json](summary.json) and [comparison.csv](comparison.csv); the parsers were not rerun. Packet bytes and parser source were inspected to explain the reported errors.

All **300,834** extracted IPv4 packets have an oracle frame. The two Spicy parsers (`GPT-5.4-IPv4` and `GPT-5.4-WNP-IPv4`) agree on every packet: 300,807 accepts and 27 rejects. The 3D parser accepts every packet. Of the oracle verdicts, 300,827 are ACCEPT and seven are STRICT_REJECT.

## The 27 parser disagreements with oracle ACCEPT

Both Spicy parsers reject these same 27 frames; 3D accepts them.

| Capture and frames | Count | Observed cause |
| --- | ---: | --- |
| `220614_ip_flags_google.pcapng`: 34, 36, 38, 40, 42, 44, 46, 48, 50, 52, 54–58 | 15 | The high reserved IPv4 flag bit is set (`0x8000` in the flags/fragment field; some packets also set DF/MF). Both Spicy grammars require that bit to be zero. Wireshark recognizes the IPv4 packet without marking it malformed. |
| `220614_ip_flags_google.pcapng`: 35, 37, 39, 41, 43, 45, 47, 49, 51, 53 | 10 | The IP `total_length` is 28 bytes, but the extracted byte string is 46 bytes: 18 zero bytes follow the IP datagram. This yields a 60-byte Ethernet frame including its 14-byte header, consistent with Ethernet minimum-frame padding. Both Spicy grammars require `trailing_data` to be empty and reject these extracted bytes. The oracle uses the IP length and accepts the datagram. |
| `log4.pcap`: 67439, 164789 | 2 | The IPv4 header checksum field is `0x0000`. Both Spicy grammars require a nonzero checksum field; the oracle does not classify these frames as malformed. This check tests nonzero presence, not checksum correctness. |

The first group is a strict grammar validation versus a permissive oracle verdict. The second is an **extraction boundary issue**: link padding is present in the bytes handed to a parser that expects exactly the IP datagram. The third is a grammar policy for a zero checksum field.

## Seven oracle STRICT_REJECT frames accepted by all parsers

`log4.pcap` frames **17693, 23067, 25933, 142270, 215577, 222923, 241303** are accepted by all three IPv4 parsers. The oracle marks `_ws.malformed` at a deeper layer: SSH on the first three, `lbmsrs` on the next three, and LDAP on 241303 (an LDAP recursion-depth assertion). The extracted IP packets have ordinary version, length, and nonzero header-checksum fields. These verdicts do not establish an IPv4 grammar failure because the IPv4 parsers do not parse those application protocols.

The 3D parser's 100% accept rate also needs context: [IPv4.3d](../../../IPv4.3d) has no comparable checks for the reserved flag, declared IP length versus trailing bytes, or header checksum. Its acceptance of the 27 packets shows less validation, not a demonstrated ability to handle those cases correctly.
