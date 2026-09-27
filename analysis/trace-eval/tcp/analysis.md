# TCP trace comparison

This analysis uses the existing [summary.json](summary.json) and [comparison.csv](comparison.csv); the parsers were not rerun. Packet bytes and parser source were inspected to explain the reported errors.

The oracle reports **305,468** TCP frames; **305,466** have extracted TCP bytes. The two oracle-only frames are `log-bravo.pcap` **17763** and **17766**: Wireshark identifies TCP inside an ICMP-quoted IP packet, while the extractor did not produce standalone TCP payloads for those frames. They are coverage gaps.

The No-Spec Spicy parser accepts all 305,466 extracted segments. The standard Spicy and 3D parsers each reject the **same 20** segments. All 20 are oracle ACCEPT in `log4.pcap`.

## Every target disagreement: ECN flags

| Frames in `log4.pcap` | Count | TCP control byte | Cause |
| --- | ---: | --- | --- |
| 98813, 98818, 100235, 100238, 100240, 100241, 102368, 102378, 102379, 102380, 213818, 213838, 213840, 213843, 215913, 215914, 215916, 215918 | 18 | `0xc2` | CWR, ECE, and SYN are set. |
| 98896, 98902 | 2 | `0x52` | ECE, ACK, and SYN are set. |

The standard Spicy grammar and [TCP.3d](../../../TCP.3d) treat **all six bits** between the data-offset nibble and URG as reserved and require zero. That includes the CWR and ECE positions, so they reject these ECN-related SYN segments. The No-Spec grammar names CWR and ECE separately and accepts them, matching the oracle. Standard Spicy reports `reserved bits must be zero`; 3D reports a bitfield constraint failure. This is an overly broad reserved-bit check in those two parsers.

## Seven oracle STRICT_REJECT frames accepted by all parsers

`log4.pcap` frames **17693, 23067, 25933, 142270, 215577, 222923, 241303** are accepted by all three TCP parsers. The oracle's `_ws.malformed` comes from deeper SSH, `lbmsrs`, or LDAP dissection, as described in the [IPv4 analysis](../ipv4/analysis.md). These are not evidence of bad TCP headers. The matched comparison has no TCP parser accept of an oracle-marked TCP-header error.
