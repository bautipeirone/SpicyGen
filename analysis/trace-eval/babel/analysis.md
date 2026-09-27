# Babel trace comparison

This analysis uses the existing [summary.json](summary.json) and [comparison.csv](comparison.csv); the parsers were not rerun. The three disagreeing payloads and their saved oracle reports were inspected. TShark verbose output was used to locate its reported exception.

All **21,639** extracted Babel payloads have oracle frames. `BABEL-GPT` and `BABEL-No-Spec` agree on **every payload** and accept all 21,639. The oracle accepts 21,636 and marks three as `STRICT_REJECT`; therefore, there are no disagreements between the two target parsers, only three oracle-versus-target disagreements.

## The three disagreeing frames

| Capture, frame | Payload and oracle result | Cause established by the evidence |
| --- | --- | --- |
| `babel_rtt.pcap` 2 | 44-byte Babel v2 payload, body length 40. Hello TLV length 12 with a four-byte timestamp sub-TLV; IHU TLV length 24 with an eight-byte timestamp sub-TLV. Oracle `_ws.malformed`; both parsers ACCEPT. | TShark decodes the Hello and IHU through the IHU timestamp-origin field, then reports `Malformed Packet (Exception occurred)` without naming an invalid wire field. |
| `babel_rtt.pcap` 3 | Same TLV structure and verdicts, with different timestamps and peer address. | Same Babel dissector exception while processing the IHU timestamp sub-TLV. |
| `babel_rtt.pcap` 8 | Same TLV structure and verdicts, with different timestamps. | Same Babel dissector exception while processing the IHU timestamp sub-TLV. |

Both generated grammars accept the declared TLV lengths and treat timestamp sub-TLV content as generic bytes. Thus they do not validate RTT timestamp semantics. TShark 4.2.2 labels these three frames malformed because its Babel dissector **throws an exception**, not because the report identifies a failed Babel field constraint. All three payloads end immediately after the IHU timestamp sub-TLV. In the same capture, frame 9 contains Hello and IHU timestamp sub-TLVs **followed by more TLVs** and is accepted by TShark. That pattern points to a possible end-of-message handling issue in the dissector, but the available result does **not** establish whether the three RTT values are invalid or the dissector fails on otherwise valid values. A specific wire-level cause cannot be assigned from this oracle report.

The practical conclusion is limited: both parsers behave identically on this trace, and their three apparent false accepts are concentrated in RTT timestamp handling. The remaining 21,636 oracle-accepted frames agree with both parsers.
