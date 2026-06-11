# Spicy notes
This repository provides functionality for using LLMs to generate spicy grammars
for protocols specifications from their RFCs.

## Requirements
* Spicy toolkit, or at least the spicy compiler `spicyc`.
* Python 3.10+
* Ollama, Google Gemini, or OpenAI API keys.

## Setup
1. Install python dependencies with `pip install -r requirements.txt`
2. Set environment variables for API keys in `.env` file. Only the ones that will be used need to be setup.
```bash
OLLAMA_API_KEY="ollama_api_key"
GOOGLE_API_KEY="google_api_key"
OPENAI_API_KEY="openai_api_key"
ANTHROPIC_API_KEY="anthropic_api_key"
```

## Usage
The main script is `generate_grammar.py`. It is run like this:
```bash
python generate_grammar.py --model MODEL_ID --target-language spicy -o OUTPUT_FILE <protocol>
```
The `MODEL_ID` must be one of the keys defined in `model_index.json` (e.g., `qwen3-coder` or `gemini-3.1-flash`).
Necessary API keys required by the model's provider must be set in the `.env` file.
The script also accepts optional parameters like `--config` to provide generation parameters, `--debug-dir` to save refinement steps, `--metrics-file`, and `--target-language` to switch the prompt/toolchain/runtime backend together.

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
You can use the provided Dockerfiles to run the benchmark script in isolated environments. The shared Python application setup lives in `docker/install_app.sh`, while each runtime has its own Dockerfile so it can build on the base image that best matches its toolchain.

1. Build the default Python runtime image:
```bash
docker build -t grammar-generator .
```

2. Run the image, providing your `.env` file and mounting an output directory to retrieve the results. You can pass arguments directly to `benchmark.sh`:
```bash
docker run --rm --env-file .env -v $(pwd)/outputs:/app/outputs grammar-generator -m "gemini-3.1-flash" -p "dhcp dns"
```

### Backend-Specific Images
Use the runtime-specific Dockerfiles when you want to build an image with a backend toolchain included:

```bash
docker build -f Dockerfile.spicy -t grammar-generator:spicy .
docker build -f Dockerfile.3d -t grammar-generator:3d .
```

The intended pattern is:
- `runtime`: Python app and shared utilities only.
- `runtime-spicy`: Python app plus the Spicy toolchain.
- `runtime-3d`: Python app plus the EverParse/3D toolchain, built directly on the EverParse image so `3d.exe` matches its required system libraries.

The Spicy image currently installs the official Debian 12 Spicy package from a pinned release. You can override the version or the package URL at build time:

```bash
docker build -f Dockerfile.spicy --build-arg SPICY_VERSION=v1.16.0 -t grammar-generator:spicy .
docker build -f Dockerfile.spicy --build-arg SPICY_DEB_URL=https://github.com/zeek/spicy/releases/download/v1.16.0/spicy_linux_debian12.deb -t grammar-generator:spicy .
```

If you are building on a non-`amd64` platform, you may need to override `SPICY_DEB_URL` to a compatible artifact or build the image for `linux/amd64`.

This is usually cleaner than forcing multiple backend runtimes into one image, because each final image stays explicit and can follow the system libraries expected by its toolchain.

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
