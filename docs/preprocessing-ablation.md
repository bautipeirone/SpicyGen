The evaluation in [preprocessing-ablation.yaml](../evals/preprocessing-ablation.yaml)
compares level 00 of the tool ablation with the full framework after disabling
only `agent.enable_preprocessing`. The second arm uses
[ablation-no-preprocessing.yaml](../configs/evals/ablation-no-preprocessing.yaml).
Unlike the original comparison between levels 02 and 03, both arms retain
packet representations and parse-tree feedback. They also share the models,
language instructions, protocol specifications from `protocol_index.json`,
15-attempt budget, RFC retrieval, and negative-packet checks.

The Spicy preprocessor checks field identifiers against reserved keywords
(including `type`, `time`, `port`, and `file`) and reports bitfields missing an
explicit `&bit-order`. It provides feedback before invoking the compiler; it
does not rename fields, infer the correct bit order, validate TLVs, or check
time conversions. The expectation is that protocols with these layouts and
names give the preprocessor more opportunities to help. Whether a generated
grammar actually triggers either check must be established from its output.

The five protocols are basic binary protocols already present in the index.
The RFC versions below match that index rather than substituting newer
specifications in either arm.

| Protocol | Relevant specification features | Existing positive / negative packets |
| --- | --- | --- |
| `ipv4` | Version/IHL, flags/fragment offset, and the copied/class/number bits inside an option type; type/length/data options and TTL. [RFC 791, section 3.1](https://www.rfc-editor.org/rfc/rfc791.html#section-3.1). | 5 / 8 |
| `tcp` | Data offset, reserved bits, six control bits, and kind/length/data options. [RFC 793, section 3.1](https://www.rfc-editor.org/rfc/rfc793.html#section-3.1). | 11 / 8 |
| `icmpv4` | An explicit `Type` field exercises reserved-name handling without requiring a packed header. Timestamp request/reply formats also define three time fields. [RFC 792](https://www.rfc-editor.org/rfc/rfc792.html). | 7 / 8 |
| `dhcp` | Broadcast flag plus reserved bits, `secs`, the `file` field, and TLV options containing lease, renewal, and rebinding times. [RFC 2131, section 2](https://www.rfc-editor.org/rfc/rfc2131.html#section-2), [RFC 2132, sections 9.2 and 9.11–9.12](https://www.rfc-editor.org/rfc/rfc2132.html#section-9.2). | 6 / 10 |
| `dns` | Packed header flags and resource records containing `TYPE`, TTL, RDLENGTH, and RDATA. These records have additional name/class/time fields, so they are not plain TLVs. [RFC 1035, sections 4.1.1 and 4.1.3](https://www.rfc-editor.org/rfc/rfc1035.html#section-4.1.1). | 16 / 10 |

Every positive packet for these five protocols has an expected `.tree`, so
parse-tree feedback remains available throughout the evaluation. The corpus
already exposes IPv4 header bitfields and options, TCP control bits, DNS flags
and TTLs, and DHCP elapsed seconds and lease/renewal/rebinding option values.
ICMPv4 supplies a complementary field-name case; its current fixtures cover
echo, unreachable, redirect, and time-exceeded messages, but no timestamp
request/reply. A time-exceeded message does not itself contain a timestamp.
TCP expected trees retain options as opaque bytes, and the IPv4 fixtures do
not contain timestamp options. These cases therefore do not establish full
option or timestamp coverage. Also, a compound identifier such as `lease_time`
does not collide with the reserved word `time`.

[NTP, RFC 5905, section 7.3](https://www.rfc-editor.org/rfc/rfc5905.html#section-7.3)
is a strong candidate for a later extension: it combines packed LI/VN/mode
bits, timestamps, and extension type/length fields. The current NTP corpus has
four positive packets but no expected parse trees. It was left out so this
initial comparison retains the baseline's field-value feedback for all five
protocols without requiring corpus changes.

Run the matrix preview with:

```bash
uv run python run_eval.py evals/preprocessing-ablation.yaml --dry-run
```

Run generation with refinement artifacts retained for inspecting which
preprocessor checks actually occurred:

```bash
uv run python run_eval.py evals/preprocessing-ablation.yaml --debug
```

The default matrix has 20 runs: five protocols, two models, two configurations,
one language, and one sample. This matches the original ablation's sample
count and serves as a screening run. Increase `defaults.samples` for repeated
measurements before drawing statistical conclusions.

Compare final packet/parse-tree success, positive acceptance and negative
rejection counts, refinement attempts, elapsed time, and cost for each
protocol/model across the two configurations. Inspect debug compilation logs
and generated bitfields to distinguish reserved-name feedback from missing
bit-order feedback. The reported first-compile outcome includes preprocessor
rejection in the enabled arm, so it is a validation-pipeline measure rather
than an isolated compiler measure. Packet and parse-tree success criteria are
the same in both arms.
