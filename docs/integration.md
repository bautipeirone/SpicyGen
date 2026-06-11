# Walkthrough: `create_zeek_package.py`

## What was built

[create_zeek_package.py](file:///home/bautistapeirone/Desktop/spec-extract/code/grammar-generator/create_zeek_package.py) — a Python script that automates Zeek package creation from generated Spicy parsers.

### Usage

```bash
python create_zeek_package.py <spicy_file> --metadata <json_file> --output-dir <dir> [--name <name>]
```

### Metadata JSON format

```json
{"transport": "udp", "port": 53}
```

Maps to the `ProtocolMetadata` dataclass:

```python
@dataclass
class ProtocolMetadata:
    transport: str  # "tcp" or "udp"
    port: int       # well-known port number
```

### What the script does (5 phases)

| Phase | Action |
|-------|--------|
| 1 | **Parse** the `.spicy` file → extract `module_name` and `entry_unit` |
| 2 | **Scaffold** with `zkg --user create --force --user-var ...` (non-interactive) |
| 3 | **Copy** the user's Spicy parser over the template placeholder |
| 4 | **Generate** skeleton `.evt`, `zeek_*.spicy`, `main.zeek` files |
| 5 | **Update** `README.md` and `zkg.meta` |

### Key design decisions

- **Analyzer name derivation**: Strips LLM model suffixes from module names (`DNS_gpt` → `DNS`, `IRC_gemini` → `IRC`)
- **Lowercase filenames**: `zkg` creates lowercase filenames (`dns.spicy`, not `DNS.spicy`) and `CMakeLists.txt` references them — our script matches this convention
- **`zeek_*.spicy` includes protocol rejection**: The generated file has a `%error` hook that calls `zeek::reject_protocol()` on parse failures
- **Skeleton files with TODOs**: `.evt` and `.zeek` files have one placeholder event; the plan is for an LLM to fill these in later

## Verification

Tested with `dns-gpt-5.4.spicy` (module `DNS_gpt`, entry unit `Message`) + metadata `{"transport": "udp", "port": 53}`.

Generated package structure:
```
spicy-dns/
├── CMakeLists.txt
├── analyzer/
│   ├── CMakeLists.txt     # References dns.spicy dns.evt zeek_dns.spicy
│   ├── dns.spicy          # Full DNS parser (copied from input)
│   ├── dns.evt            # Skeleton: imports DNS_gpt, registers analyzer on 53/udp
│   └── zeek_dns.spicy     # Zeek_DNS module with protocol rejection hook
├── scripts/
│   ├── __load__.zeek
│   ├── main.zeek          # Skeleton: DNS module with Info record + LOG stream
│   └── dpd.sig
├── testing/               # Template tests (PCAPs to be added later)
├── README.md
└── zkg.meta               # Updated description
```

All file references in `CMakeLists.txt` match, no duplicate files, module names are consistent.
