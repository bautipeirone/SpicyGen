# Walkthrough: `create_zeek_package.py`

## What was built

[create_zeek_package.py](file:///home/bautistapeirone/Desktop/spec-extract/code/grammar-generator/create_zeek_package.py) — a Python script that automates Zeek package creation from generated Spicy parsers.

### Usage

```bash
python create_zeek_package.py <spicy_file> --metadata <json_file> --output-dir <dir> [--name <name>]
```

### Metadata JSON format

```json
{"transport": ["udp", "tcp"], "port": 53}
```

Maps to the `ProtocolMetadata` dataclass:

```python
@dataclass
class ProtocolMetadata:
    transport: str         # selected Zeek transport ("tcp" or "udp")
    port: int              # well-known port number
    transports: list[str]  # original ordered transport candidates
```

### What the script does (5 phases)

| Phase | Action |
|-------|--------|
| 1 | **Parse** the `.spicy` file → extract `module_name` and `entry_unit` |
| 2 | **Scaffold** with `zkg --user create --force --user-var ...` (non-interactive) |
| 3 | **Generate** skeleton `.evt`, `zeek_*.spicy`, `main.zeek` files |
| 4 | **Update** `README.md` and `zkg.meta` and add tests from PCAP files |
| 5 | **Install** as Zeek plugin |

### Key design decisions

- **Analyzer name derivation**: Strips LLM model suffixes from module names (`DNS_gpt` → `DNS`, `IRC_gemini` → `IRC`)
- **Lowercase filenames**: `zkg` creates lowercase filenames (`dns.spicy`, not `DNS.spicy`) and `CMakeLists.txt` references them — our script matches this convention
- **`zeek_*.spicy` includes protocol rejection**: The generated file has a `%error` hook that calls `zeek::reject_protocol()` on parse failures
