from pathlib import Path

def get_specs_stats(spec: Path) -> dict:
    """
    Extracts statistics from a specification file.

    Args:
        spec (Path): The path to the specification file.
    Returns:
        dict: A dictionary containing the statistics of the specification like number of lines, words, and characters.
    """
    if not spec.is_file():
        print(f"Error: {spec} is not a valid file.")
        return {}

    with spec.open() as f:
        content = f.read()
        lines = content.splitlines()
        words = content.split()
        chars = len(content)

    return {
        "lines": len(lines),
        "words": len(words),
        "characters": chars
    }

def summarize_specs(specs_dir: Path) -> None:
    """
    Summarizes the specifications in the given directory.

    Args:
        specs_dir (Path): The directory containing the specification files.
    """
    if not specs_dir.is_dir():
        print(f"Error: {specs_dir} is not a valid directory.")
        return

    spec_files = list(specs_dir.glob("*.txt"))
    if not spec_files:
        print(f"No specification files found in {specs_dir}.")
        return

    print(f"Found {len(spec_files)} specification files in {specs_dir}:")
    for spec_file in spec_files:
        stats = get_specs_stats(spec_file)
        print(f"{spec_file.name:40}: {stats['lines']:6} lines, {stats['words']:7} words, {stats['characters']:9} characters")


if __name__ == "__main__":
    summarize_specs(Path("specs"))