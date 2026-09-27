# HTTP/1.1 trace comparison: GPT-5.4 Spicy vs No-Spec Spicy

Run: `compare_traces.py` on `../../data/pcap/http1.1`, using the existing `../../data/packets/http/manifest.jsonl`, the `http` Wireshark oracle profile, both specified parsers, and 16 workers. See [summary.json](summary.json), [comparison.csv](comparison.csv), and the packet-level results in `targets/`.

## Results and interpretation

The parsers made **identical decisions on all 9,001 extracted payloads**: 6,786 accepts and 2,215 rejects each. Neither run had an execution error. The oracle recognized 7,380 HTTP frames. Only 6,105 frames have both an extracted payload and an oracle verdict: both parsers accepted 6,077 oracle accepts, rejected 27 oracle accepts, and rejected the one oracle strict reject with an extracted payload. There were no matched frames where a parser accepted an oracle reject.

There are **1,275 oracle-only frames** and **2,896 extracted-payload-only frames**. Those are coverage gaps, not disagreements. Of the payload-only frames, both parsers accepted 709 and rejected 2,187. The extractor selects individual TCP payloads with an HTTP-looking first line; TShark uses TCP stream reassembly and its `http` display filter. For example, in `c05-http-reply-r1.pcap`, frame 6 has a response start line but TShark labels it a TCP segment of a reassembled PDU, while frame 16 is labeled an HTTP continuation even though its raw TCP payload starts `/1.1 200 OK`. These different units of analysis explain why the raw accept totals cannot be compared with all oracle verdicts.

## Every matched disagreement

All 27 are **oracle ACCEPT / both parsers REJECT**. The parser errors and wire bytes support the following causes. Frame numbers are capture frame numbers, not packet file indices.

### `c05-http-reply-r1.pcap`: malformed header lines (14)

Both grammars require a nonempty token followed by `:` for every header. TShark recognizes these responses as HTTP and does not mark them malformed. It reports `Illegal characters found in header name` on frame 55871, but the default oracle only rejects `_ws.malformed` or expert **Error** severity. Some long variants receive an advisory about excess data after the body.

| Frame | Offending line | Cause |
| ---: | --- | --- |
| 55871 | `: timeout=5, max=10` | Empty header name. |
| 56359 | `Keep-Alive%stimeout=5, max=10` | Missing colon; `%s` text joined to the name. |
| 56369 | `Keep-Alive%s...%stimeout=5, max=10` | Missing colon after repeated `%s`. |
| 56379 | `Keep-Alive%s...%stimeout=5, max=10` | Same missing colon, longer `%s` sequence. |
| 56389 | `Keep-Alive%s...%stimeout=5, max=10` | Same missing colon, much longer `%s` sequence. |
| 56410 | `Keep-Alive%n...%ntimeout=5, max=10` | Missing colon after repeated `%n`. |
| 56420 | `Keep-Alive%n...%ntimeout=5, max=10` | Same missing colon, longer `%n` sequence. |
| 56430 | `Keep-Alive%n...%ntimeout=5, max=10` | Same missing colon, much longer `%n` sequence. |
| 56461 | `Keep-Alive%.4096dtimeout=5, max=10` | Missing colon after format text. |
| 56471 | `Keep-Alive%.8192dtimeout=5, max=10` | Missing colon after format text. |
| 56491 | `Keep-Alive%.4096d%*%ntimeout=5, max=10` | Missing colon after format text. |
| 56501 | `Keep-Alive%.8192d%*%ntimeout=5, max=10` | Missing colon after format text. |
| 56511 | `Keep-Alivetimeout=5, max=10` | `Keep-Alive` and its value run together without a colon. |
| 56679 | `Keep-Alivetimeout=5, max=10` | Same run-together field. |

For these 14, the disagreement reflects **oracle permissiveness about malformed header syntax**, rather than evidence that the grammars should accept those bytes. Both parsers report `failed to match regular expression` while parsing the header line.

### `log1.pcap`: fixed method lists (8)

Both parsers hard-code the nine familiar methods (`OPTIONS`, `GET`, `HEAD`, `POST`, `PUT`, `DELETE`, `TRACE`, `CONNECT`, `PATCH`) rather than parsing a general HTTP method token. Their start-line switch fails with `no expected look-ahead token found`. TShark recognizes all eight as HTTP requests.

| Frame | Method | Cause |
| ---: | --- | --- |
| 6985 | `PROPFIND` | Extension method absent from both lists. |
| 7015 | `PROPFIND` | Same. |
| 8434 | `UJXY` | Unlisted method token in an HTTP-shaped probe. |
| 8653 | `PROPFIND` | Same extension method. |
| 11533 | `LSGC` | Unlisted method token in an HTTP-shaped probe. |
| 11539 | `PROPFIND` | Same extension method. |
| 11802 | `PROPFIND` | Same extension method. |
| 12177 | `PROPFIND` | Same extension method. |

### Responses that need stream or request context (5)

| Capture, frame | Cause |
| --- | --- |
| `log1.pcap` 4975 | Response segment contains only headers with `Content-Length: 4369921`. Both standalone parsers require 4,369,921 body bytes in this file and find zero. TShark accepts the response header in stream context. |
| `log1.pcap` 12713 | Response to a `HEAD` request on TCP stream 95 (request at frame 12710): `Content-Length: 315` describes the representation, but the response correctly has no body. Both parsers lack request context and demand 315 bytes. |
| `log2.pcap` 28816 | Chunked response has a complete `4b` (75-byte) data chunk, but its terminating `0\r\n\r\n` is in frame 28818 (five bytes). Both parsers reach end of the isolated segment while seeking the next chunk; TShark follows the stream. |
| `log2.pcap` 35556 | Response segment contains only headers with `Content-Length: 14652829`; both parsers demand that body in the same file and find zero. |
| `log3.pcap` 34970 | Response segment contains only headers with `Content-Length: 15752479`; both parsers demand that body in the same file and find zero. |

The four framing/context cases above do not establish a malformed complete HTTP message: this comparison feeds **one TCP segment at a time** to the grammars. The `HEAD` case is a separate parser limitation because it needs the preceding request even when the response headers are complete.

## Practical conclusion

The No-Spec grammar performs exactly like the spec-based grammar on this trace at the accept/reject level. The supported method list is their shared limitation on complete requests. Many response mismatches come from comparing standalone segments with a stream-aware oracle; a stream-reassembled, request-aware comparison would be needed to judge full HTTP message handling. The malformed-header group shows that Wireshark's default HTTP recognition is more permissive than both grammars and should be reviewed as an oracle-policy choice.
