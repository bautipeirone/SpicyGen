# RTSP trace comparison

This analysis uses the existing [summary.json](summary.json) and [comparison.csv](comparison.csv); the parsers were not rerun. The comparison is **per extracted RTSP unit**. `unit_index` distinguishes multiple messages or interleaved data units in one capture frame; oracle verdicts are frame-level.

The extractor produced **220,080 units from 213,890 frames**. The oracle recognizes 213,888 frames, covering 220,078 units. `RTSP-WNP` and `RTSP-No-Spec` accept all 220,080 units. `RTSP` accepts 218,874 and rejects 1,206. Of its rejects, **1,204** are on oracle-accepted frames; two have no oracle verdict. All 1,206 are at `unit_index=0`, and none of the additional units in multi-unit frames is rejected.

## Shared first failure: `CSeq` appears after another header

The [RTSP Spicy grammar](../../../outputs/stable/rtsp-gpt-5.4.spicy) places `CSeqHeader` immediately after every request or status line (lines 153, 163, and 170). The parser stops there with `expected bytes literal "CSeq"` when the first header has another name. **Every one of the 1,206 rejected units does contain `CSeq` later in its header block.** The WNP and No-Spec grammars parse headers without imposing that order.

| Capture and frames | Count | First header; `CSeq` position | Oracle status and further detail |
| --- | ---: | --- | --- |
| `local-network.pcap`: 400 `DESCRIBE` requests | 400 | `Accept`; `CSeq` second | Oracle ACCEPT. Example frame 2882. |
| `local-network.pcap`: 400 `SETUP` requests | 400 | `Transport`; `CSeq` second | Oracle ACCEPT. Example frame 2884. |
| `local-network.pcap`: 400 `PLAY` requests | 400 | `Range`; `CSeq` second | Oracle ACCEPT. Example frame 2886. |
| `RTSPPACKETS1.pcap`: 4, 5, 15 | 3 | `User-Agent`; `CSeq` sixth | Frames 4 and 15 are oracle ACCEPT; frame 5 is a TCP retransmission with no oracle RTSP frame. These requests also carry `Accept-Charset`, `X-Accept-Authentication`, and `Supported`, which the grammar's fixed header list does not include. |
| `RTSPPACKETS1.pcap`: 7, 16 | 2 | `Date`; `CSeq` second | Frame 16 is oracle ACCEPT; frame 7 is an out-of-order TCP segment with no oracle RTSP frame. Both responses also carry `Supported`, absent from the fixed header list. |
| `rtsp_with_data_over_tcp.cap`: 1 | 1 | `Transport`; `CSeq` third | Oracle ACCEPT. The response also carries `Supported` and `Etag`, absent from the fixed header list. |

The header-order requirement fully explains the **first reported error** in all 1,206 units. For the 1,200 `local-network.pcap` cases, that is the observed parser limitation. For the six other cases, fixing the `CSeq` position alone may expose further rejects because the grammar enumerates header names instead of allowing extension fields.

The two units without an oracle frame, `RTSPPACKETS1.pcap` frames **5** and **7**, are coverage gaps: TShark labels frame 5 a TCP retransmission of the request in frame 4 and frame 7 an out-of-order TCP segment, while the extractor still emitted their RTSP-looking bytes. They should not be counted as oracle disagreements.
