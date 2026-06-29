from pathlib import Path
from dataclasses import dataclass
import json
import argparse
from typing import Any

@dataclass()
class NegativePacket:
    name: str
    protocol_name: str
    content: str | bytes
    invalid_category: str | None = None
    reason: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NegativePacket":
        name = d["name"]
        protocol_name = d["protocol"]
        content = d["packet"]
        if isinstance(content, list): # Cast list of bytes to bytes array
            content = bytes(content)
        invalid_category = d.get("invalid_category")
        reason = d.get("reason")
        return cls(
            name=name,
            protocol_name=protocol_name,
            content=content,
            invalid_category=invalid_category,
            reason=reason
        )

def write_packet(packet: NegativePacket, output_dir: Path):
    filename = "_".join([ token.lower() for token in packet.name.split(" ") ])
    packet_path = output_dir / f"{filename}.bin"
    info_path = output_dir / f"{filename}.bin.info"
    match packet.content:
        case str():
            packet_path.write_text(packet.content)
        case bytes():
            packet_path.write_bytes(packet.content)
    if packet.invalid_category or packet.reason:
        info = []
        if packet.invalid_category:
            info.append(f"Category: {packet.invalid_category}")
        if packet.reason:
            info.append(f"Invalid Reason: {packet.reason}")
        info_path.write_text("\n".join(info))

def save_invalid_packets(packets_file: Path, output_dir):
    raw_packets = packets_file.read_text().splitlines()
    for raw_packet in raw_packets:
        packet = NegativePacket.from_dict(json.loads(raw_packet))
        write_packet(packet, output_dir=output_dir)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Save invalid packets from a .jsonl file"
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="Input file to read packets from."
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Directory to write the packets."
    )
    
    return parser.parse_args()

def main():
    args = parse_args()
    save_invalid_packets(args.input_file, args.output_dir)

if __name__ == "__main__":
    raise SystemExit(main())