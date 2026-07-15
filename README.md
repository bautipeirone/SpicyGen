# Spicy notes
This repository provides functionality for using LLMs to generate spicy grammars
for protocols specifications from their RFCs.

## Requirements
* Spicy toolkit, or at least the spicy compiler `spicyc`.
* Python 3.10+
* Ollama, Google Gemini, or OpenAI API keys.

## Setup
1. Install Python dependencies with `uv sync`
2. Set environment variables for API keys in `.env` file. Only the ones that will be used need to be setup.
```bash
OLLAMA_API_KEY="ollama_api_key"
OLLAMA_HOST_ENDPOINT="http://localhost:11434/v1/"
GEMINI_API_KEY="gemini_api_key"
OPENAI_API_KEY="openai_api_key"
ANTHROPIC_API_KEY="anthropic_api_key"
```

For the `ollama` provider, this project is configured through AutoGen as an OpenAI-compatible client, so `OLLAMA_HOST_ENDPOINT` must point at an OpenAI-compatible Ollama endpoint such as `http://localhost:11434/v1/`.

## Usage
The main script is `generate_grammar.py`. It is run like this:
```bash
python generate_grammar.py --model MODEL_ID --target-language spicy -o OUTPUT_FILE <protocol>
```
The `MODEL_ID` must be one of the keys defined in `model_index.json` (e.g., `qwen3-coder` or `gemini-3.1-flash`).
Necessary API keys required by the model's provider must be set in the `.env` file.
The script also accepts optional parameters like `--config` to provide generation parameters, `--debug-dir` to save refinement steps, `--metrics-file`, and `--target-language` to switch the prompt/toolchain/runtime backend together.
When the project is installed through `uv`, the same commands are also available as console scripts such as `generate-grammar`, `preprocess-spec`, `run-pipeline`, and `create-zeek-package`.

Another possibility to test many models along various protocols is to use the shell script.
```bash
./benchmark.sh -m "qwen3-coder gemini-3.1-flash" -p "tftp dhcp http" -t spicy --output-dir path_to_output
```
This will run the grammar generator for each combination of the specified models and protocols. It will also try to compile the result with `spicyc` to test if the LLM produced a valid grammar.
To keep track of each run, a `README.md` file is created in the output directory and metrics are aggregated. The flag `--note` can be used to add a note to the run, and `--debug` to save debug logs.

### Standalone Specification Override
By default, `generate_grammar.py` resolves the protocol specification using the metadata inside `protocol_index.json`. You can override this behavior by passing the `--spec-file` flag with a path to any custom text file (such as a preprocessed summary).

This is highly useful for isolated manual testing, debugging custom prompts, or validating how the LLM reacts to specific cuts of the specification.

## Docker Usage
You can use the provided Dockerfiles to run the generator in isolated environments.

### Backend-Specific Images
Use the runtime-specific Dockerfiles when you want to build an image with a backend toolchain included:

```bash
docker build -f Dockerfile.spicy -t grammar-generator:spicy .
docker build -f Dockerfile.3d -t grammar-generator:3d .
```

Run the installed console script you need explicitly:

```bash
docker run --rm --env-file .env grammar-generator:spicy generate-grammar --model gemini-3.1-flash --target-language spicy -o /tmp/dns.spicy dns
docker run --rm --env-file .env grammar-generator:3d run-pipeline run dns -m gemini-3.1-flash -t 3d
```

```bash
docker build -f Dockerfile.spicy --build-arg SPICY_VERSION=v1.16.0 -t grammar-generator:spicy .
docker build -f Dockerfile.spicy --build-arg SPICY_DEB_URL=https://github.com/zeek/spicy/releases/download/v1.16.0/spicy_linux_debian12.deb -t grammar-generator:spicy .
```

If you are building on a non-`amd64` platform, you may need to override `SPICY_DEB_URL` to a compatible artifact or build the image for `linux/amd64`.

## Testing
To run unit tests and integration tests run `pytest`.

## Adding Models and Protocols
To extend the program's capabilities, you can easily add new LLMs or new protocols by updating the respective index files:

### Adding a Model (`model_index.json`)
Add a new key with the model's identifier. Required fields include:
- `name`: The exact model name expected by the provider API.
- `provider`: The LLM provider (e.g., `google`, `ollama`, `openai`, `anthropic`).
- `context-window`: The maximum context size for the model.
- `cutoff-date` / `price`: Optional metadata for tracking.

### Adding a Protocol (`protocol_index.json`)
Add a new key with the protocol's short name. Required fields include:
- `type`: The type of protocol (`binary`, `text`).
- `spec_type`: The origin of the specification (e.g., `rfc` or `bluebook`).
- `rfc_id`: The RFC number (if `spec_type` is `rfc`).
- `local_spec_file`: (Optional) Path to a specific local text file to use instead of fetching an RFC.

#### External Parser Arguments

If parsing depends on managed configuration that is not encoded in packet bytes, declare a protocol-level argument contract in the protocol entry:

```json
"parser_arguments": [
  {"name": "frame_length", "type": "uint64", "description": "Configured frame length."},
  {"name": "has_fecf", "type": "bool", "description": "Whether an FECF is present."}
]
```

The default `type` is used for every target language. If one backend needs a different concrete type spelling, it can be overriden per language:

```json
{
  "name": "frame_length",
  "type": {
    "spicy": "uint64",
    "3d": "uint32"
  }
}
```

Store each packet's values next to its bytes in `<packet>.bin.args.json`:

```json
{
  "frame_length": 16,
  "has_fecf": true
}
```

The argument names and target-resolved types form the contract presented to the LLM. At runtime, the generated parser signature and sidecar keys are validated against that contract. Values are bound by name and reordered to match the generated signature before a packet-specific wrapper is compiled. Spicy sidecars support `bool`, signed and unsigned 8/16/32/64-bit integers, `string`, `real`, and `double`.
