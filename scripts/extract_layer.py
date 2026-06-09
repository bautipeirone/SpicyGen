import argparse
import concurrent.futures
import hashlib
import importlib
import os
import re
import sys

from scapy.packet import Packet
from scapy.utils import PcapReader


COMMON_PROTOCOL_ALIASES = {
    "arp": ("scapy.layers.l2", "ARP"),
    "dhcp": ("scapy.layers.dhcp", "DHCP"),
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


def resolve_protocol(protocol_name):
    protocol_name = protocol_name.strip()
    normalized = protocol_name.lower()

    if normalized in COMMON_PROTOCOL_ALIASES:
        module_name, class_name = COMMON_PROTOCOL_ALIASES[normalized]
        return load_protocol_class(module_name, class_name)

    if "." in protocol_name:
        module_name, class_name = protocol_name.rsplit(".", 1)
        return load_protocol_class(module_name, class_name)

    for module_name in SEARCH_MODULES:
        try:
            return load_protocol_class(module_name, protocol_name)
        except (AttributeError, ImportError, TypeError):
            continue

    known_aliases = ", ".join(sorted(COMMON_PROTOCOL_ALIASES))
    raise ValueError(
        f"Unsupported protocol '{protocol_name}'. Use one of: {known_aliases}, "
        "a known Scapy class name such as DNS, or a fully qualified Scapy class "
        "path such as scapy.layers.inet6.IPv6ExtHdrFragment."
    )


def make_file_stem(protocol_name, protocol_class):
    preferred_name = protocol_name.split(".")[-1] if "." in protocol_name else protocol_name
    preferred_name = preferred_name.strip() or protocol_class.__name__
    preferred_name = preferred_name.lstrip("_") or protocol_class.__name__.lstrip("_")
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


def extract_packets_from_capture(capture_file, capture_index, protocol_class, file_stem, output_dir):
    count = 0

    with PcapReader(capture_file) as capture:
        for pkt in capture:
            if not pkt.haslayer(protocol_class):
                continue

            target_layer = pkt[protocol_class]
            output_name = build_output_file_name(file_stem, capture_file, capture_index, count)
            write_packet_bytes(output_dir, output_name, bytes(target_layer))
            count += 1

    return count


def extract_packets_from_capture_worker(capture_file, capture_index, protocol_name, output_dir):
    load_base_layers()
    protocol_class = resolve_protocol(protocol_name)
    file_stem = make_file_stem(protocol_name, protocol_class)
    return extract_packets_from_capture(
        capture_file,
        capture_index,
        protocol_class,
        file_stem,
        output_dir,
    )


def extract_protocol_packets(input_path, output_dir, protocol_name, workers=None):
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"The input file '{input_path}' was not found.")

    capture_files = get_capture_files(input_path)

    os.makedirs(output_dir, exist_ok=True)

    load_base_layers()
    protocol_class = resolve_protocol(protocol_name)

    if not capture_files:
        print("No capture files found to process.")
        return 0

    max_workers = workers or min(len(capture_files), (os.cpu_count() or 1))
    max_workers = max(1, max_workers)

    print(
        f"Processing {len(capture_files)} capture files and extracting '{protocol_class.__name__}' "
        f"layers with {max_workers} workers into '{output_dir}'..."
    )

    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                extract_packets_from_capture_worker,
                capture_file,
                capture_index,
                protocol_name,
                output_dir,
            )
            for capture_index, capture_file in enumerate(capture_files)
        ]
        count = sum(future.result() for future in concurrent.futures.as_completed(futures))

    print(f"Extraction complete. Saved {count} packet(s) to '{output_dir}'.")
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
            "udp, dns, dhcp, arp, icmp, icmpv6, or "
            "scapy.layers.inet6.IPv6ExtHdrFragment."
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
