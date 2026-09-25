import argparse
import concurrent.futures
import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

from scapy.layers.inet import TCP
from scapy.packet import Packet
from scapy.utils import PcapReader
import scapy.layers.dot11
import scapy.layers.vxlan


COMMON_PROTOCOL_ALIASES = {
    "arp": ("scapy.layers.l2", "ARP"),
    "dhcp": ("scapy.layers.dhcp", "BOOTP"),
    "dns": ("scapy.layers.dns", "DNS"),
    "icmp": ("scapy.layers.inet", "ICMP"),
    "icmpv6": ("scapy.layers.inet6", "_ICMPv6"),
    "ip": ("scapy.layers.inet", "IP"),
    "ipv4": ("scapy.layers.inet", "IP"),
    "ipv6": ("scapy.layers.inet6", "IPv6"),
    "tcp": ("scapy.layers.inet", "TCP"),
    "udp": ("scapy.layers.inet", "UDP"),
}

SEARCH_MODULES = (
    "scapy.layers.inet",
    "scapy.layers.inet6",
    "scapy.layers.l2",
    "scapy.layers.dns",
    "scapy.layers.dhcp",
    "scapy.layers.tftp",
)

@dataclass(frozen=True)
class ProtocolHandler:
    display_name: str
    file_stem: str
    extractor: Callable[[Packet], Packet | bytes | bytearray | memoryview | None]


def load_base_layers():
    # Load the core L2/L3/L4 bindings before opening captures so Scapy can
    # decode Ethernet/IP packets instead of falling back to raw bytes.
    for module_name in ("scapy.layers.l2", "scapy.layers.inet", "scapy.layers.inet6"):
        importlib.import_module(module_name)


def load_protocol_class(module_name, class_name):
    module = importlib.import_module(module_name)
    protocol_class = getattr(module, class_name)

    if not isinstance(protocol_class, type) or not issubclass(protocol_class, Packet):
        raise TypeError(f"{module_name}.{class_name} is not a Scapy Packet class.")

    return protocol_class


def load_protocol_attribute(module_name, attribute_name):
    module = importlib.import_module(module_name)
    return getattr(module, attribute_name)


def build_layer_extractor(protocol_class):
    def extract_layer(pkt):
        if not pkt.haslayer(protocol_class):
            return None
        return pkt[protocol_class]

    return extract_layer


def get_original_tcp_payload(pkt):
    if not pkt.haslayer(TCP):
        return b""

    tcp = pkt[TCP]
    # Application dissectors may expose only the message body as Raw. The
    # captured TCP segment preserves the complete bytes on the wire.
    if tcp.original and tcp.dataofs is not None:
        return tcp.original[tcp.dataofs * 4:]
    return bytes(tcp.payload)


def detect_http_payload(pkt):
    payload = get_original_tcp_payload(pkt)

    if not payload:
        return None

    first_line = payload.split(b"\n", 1)[0].rstrip(b"\r")
    if re.fullmatch(rb"HTTP/[0-9]+(?:\.[0-9]+)? [0-9]{3}(?: [^\r\n]*)?", first_line):
        return payload
    if re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+ [^ \r\n]+ HTTP/[0-9]+(?:\.[0-9]+)?", first_line):
        return payload
    # HTTP/0.9 requests have no version or headers.
    if re.fullmatch(rb"GET [^ \r\n]+", first_line):
        return payload

    return None


def detect_rtsp_payload(pkt):
    payload = get_original_tcp_payload(pkt)
    if not payload:
        return None

    first_line = payload.split(b"\n", 1)[0].rstrip(b"\r")
    if re.fullmatch(rb"RTSP/[0-9]+(?:\.[0-9]+)? [0-9]{3}(?: [^\r\n]*)?", first_line):
        return payload
    if re.fullmatch(
        rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+ [^ \r\n]+ RTSP/[0-9]+(?:\.[0-9]+)?",
        first_line,
    ):
        return payload

    return None


BUILTIN_PROTOCOL_DETECTORS = {
    "http": detect_http_payload,
    "rtsp": detect_rtsp_payload,
}


def make_protocol_handler(protocol_name, extractor, display_name=None):
    return ProtocolHandler(
        display_name=display_name or protocol_name,
        file_stem=make_file_stem(protocol_name, display_name),
        extractor=extractor,
    )


def resolve_protocol(protocol_name):
    protocol_name = protocol_name.strip()
    normalized = protocol_name.lower()

    if normalized in BUILTIN_PROTOCOL_DETECTORS:
        return make_protocol_handler(protocol_name, BUILTIN_PROTOCOL_DETECTORS[normalized], display_name=normalized.upper())

    if normalized in COMMON_PROTOCOL_ALIASES:
        module_name, class_name = COMMON_PROTOCOL_ALIASES[normalized]
        protocol_class = load_protocol_class(module_name, class_name)
        return make_protocol_handler(protocol_name, build_layer_extractor(protocol_class), display_name=protocol_class.__name__)

    if "." in protocol_name:
        module_name, class_name = protocol_name.rsplit(".", 1)
        protocol_attribute = load_protocol_attribute(module_name, class_name)
        if isinstance(protocol_attribute, type) and issubclass(protocol_attribute, Packet):
            return make_protocol_handler(
                protocol_name,
                build_layer_extractor(protocol_attribute),
                display_name=protocol_attribute.__name__,
            )
        if callable(protocol_attribute):
            return make_protocol_handler(protocol_name, protocol_attribute, display_name=class_name)
        raise TypeError(
            f"{module_name}.{class_name} is neither a Scapy Packet class nor a callable detector function."
        )

    for module_name in SEARCH_MODULES:
        try:
            protocol_class = load_protocol_class(module_name, protocol_name)
            return make_protocol_handler(protocol_name, build_layer_extractor(protocol_class), display_name=protocol_class.__name__)
        except (AttributeError, ImportError, TypeError):
            continue

    known_aliases = ", ".join(sorted({*COMMON_PROTOCOL_ALIASES, *BUILTIN_PROTOCOL_DETECTORS}))
    raise ValueError(
        f"Unsupported protocol '{protocol_name}'. Use one of: {known_aliases}, "
        "a known Scapy class name such as DNS, a fully qualified Scapy class "
        "path such as scapy.layers.inet6.IPv6ExtHdrFragment, or a fully qualified "
        "callable detector such as custom_protocols.detect_http."
    )


def make_file_stem(protocol_name, fallback_name=None):
    preferred_name = protocol_name.split(".")[-1] if "." in protocol_name else protocol_name
    preferred_name = preferred_name.strip() or fallback_name or "packet"
    preferred_name = preferred_name.lstrip("_") or str(fallback_name or "packet").lstrip("_")
    return re.sub(r"[^0-9a-zA-Z]+", "_", preferred_name).strip("_").lower() or "packet"


def get_capture_files(path: str) -> list[str]:
    if os.path.isfile(path):
        return [path]
    capture_files = []
    for capture_file in sorted(os.listdir(path)):
        full_path = os.path.join(path, capture_file)
        if os.path.isfile(full_path):
            capture_files.append(full_path)
    return capture_files


def build_output_file_name(file_stem, capture_file, capture_index, packet_index):
    capture_stem = os.path.splitext(os.path.basename(capture_file))[0]
    capture_stem = re.sub(r"[^0-9a-zA-Z]+", "_", capture_stem).strip("_").lower() or "capture"
    return f"packet_{file_stem}_{capture_stem}_{capture_index}_{packet_index}.bin"


def write_packet_bytes(output_dir, output_name, payload):
    output_file = os.path.join(output_dir, output_name)

    try:
        with open(output_file, "xb") as bin_file:
            bin_file.write(payload)
        return output_file
    except FileExistsError:
        # Keep parallel extraction safe even if a filename already exists from
        # a previous run or from unexpected overlapping capture names.
        digest = hashlib.sha1(payload).hexdigest()[:12]
        base_name = os.path.splitext(output_name)[0]
        for suffix in range(1000):
            alternate_name = f"{base_name}_{digest}_{suffix}.bin"
            alternate_path = os.path.join(output_dir, alternate_name)
            try:
                with open(alternate_path, "xb") as bin_file:
                    bin_file.write(payload)
                return alternate_path
            except FileExistsError:
                continue
        raise FileExistsError(f"Could not find a unique output filename for '{output_name}'.")


def serialize_extracted_layer(extracted_layer):
    if extracted_layer is None:
        return None
    if isinstance(extracted_layer, Packet):
        return bytes(extracted_layer)
    if isinstance(extracted_layer, (bytes, bytearray, memoryview)):
        return bytes(extracted_layer)
    raise TypeError(
        "Protocol detector returned an unsupported type. Expected a Scapy Packet, bytes-like object, or None."
    )


def extract_packets_from_capture(capture_file, capture_index, protocol_handler, output_dir):
    records = []

    with PcapReader(capture_file) as capture:
        for frame_number, pkt in enumerate(capture, start=1):
            extracted_layer = protocol_handler.extractor(pkt)
            payload = serialize_extracted_layer(extracted_layer)
            if payload is None:
                continue

            packet_index = len(records)
            output_name = build_output_file_name(
                protocol_handler.file_stem, capture_file, capture_index, packet_index
            )
            output_file = write_packet_bytes(output_dir, output_name, payload)
            records.append({
                "packet_path": os.path.abspath(output_file),
                "capture_path": os.path.abspath(capture_file),
                "frame_number": frame_number,
                "capture_index": capture_index,
                "packet_index": packet_index,
                "sha256": hashlib.sha256(payload).hexdigest(),
            })

    return records


def extract_packets_from_capture_worker(capture_file, capture_index, protocol_name, output_dir):
    load_base_layers()
    protocol_handler = resolve_protocol(protocol_name)
    return extract_packets_from_capture(
        capture_file,
        capture_index,
        protocol_handler,
        output_dir,
    )


def extract_protocol_packets(input_path, output_dir, protocol_name, workers=None):
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"The input file '{input_path}' was not found.")

    capture_files = get_capture_files(input_path)

    os.makedirs(output_dir, exist_ok=True)

    load_base_layers()
    protocol_handler = resolve_protocol(protocol_name)

    if not capture_files:
        print("No capture files found to process.")
        return 0

    max_workers = workers or min(len(capture_files), (os.cpu_count() or 1))
    max_workers = max(1, max_workers)

    print(
        f"Processing {len(capture_files)} capture files and extracting '{protocol_handler.display_name}' "
        f"layers with {max_workers} workers into '{output_dir}'..."
    )

    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_capture = {
            executor.submit(
                extract_packets_from_capture_worker,
                capture_file,
                capture_index,
                protocol_name,
                output_dir,
            ): (capture_index, capture_file)
            for capture_index, capture_file in enumerate(capture_files)
        }
        records_by_capture = {}
        for future in concurrent.futures.as_completed(future_to_capture):
            capture_index, capture_file = future_to_capture[future]
            try:
                records_by_capture[capture_index] = future.result()
            except Exception as exc:
                raise RuntimeError(f"Failed to process '{capture_file}': {exc}") from exc

    manifest_path = os.path.join(output_dir, "manifest.jsonl")
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output_dir, prefix=".manifest-", delete=False
    ) as temporary:
        temporary_path = temporary.name
        try:
            count = 0
            for capture_index in sorted(records_by_capture):
                for record in records_by_capture[capture_index]:
                    temporary.write(json.dumps(record) + "\n")
                    count += 1
            temporary.flush()
            os.replace(temporary_path, manifest_path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    print(f"Extraction complete. Saved {count} packet(s) to '{output_dir}'.")
    print(f"Frame index written to '{manifest_path}'.")
    return count


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Extract raw bytes for a specific protocol layer from a capture file. "
            "Scapy handles .pcap, .pcapng, and gzipped variants automatically."
        )
    )
    parser.add_argument(
        "input_path",
        help="Path to the input capture or directory with capture files (.pcap, .pcapng, .pcap.gz, .pcapng.gz, ...)",
    )
    parser.add_argument(
        "-p",
        "--protocol",
        default="ipv6",
        help=(
            "Protocol layer to extract (default: ipv6). Examples: ipv4, ipv6, tcp, "
            "udp, dns, dhcp, arp, icmp, icmpv6, http, rtsp, or scapy.layers.inet6.IPv6ExtHdrFragment."
        ),
    )
    parser.add_argument(
        "--out",
        dest="output_dir",
        default="extracted_raw_packets",
        help="Output directory (default: extracted_raw_packets)",
    )
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=None,
        help="Number of capture files to process in parallel (default: CPU count, capped by file count)",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.workers is not None and args.workers < 1:
            raise ValueError("--workers must be at least 1.")
        extract_protocol_packets(args.input_path, args.output_dir, args.protocol, workers=args.workers)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
