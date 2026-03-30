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
python generate_grammar.py --provider PROVIDER --model MODEL -o OUTPUT_FILE <rfc_file>
```
Provider is the LLM client that hosts the model. It can be one of the following: ollama, google, anthropic or openai.
Necessary API keys required by each one have to be set. When using the python script, the RFC may not need to be specified,
instead the RFC ID can be provided.

Another possibility to test many models along various protocols is to use the shell script.
```bash
./benchmark.sh -m "ollama/qwen3-vl:235b-instruct-cloud ollama/gpt-oss:120b-cloud google/gemini-3-flash-preview"
               -p "tftp dhcp http" --rfc-dir path_to_rfc_dir --output-dir path_to_output
```
This will look for RFCs with the pattern `path_to_rfc_dir/*-rfc.txt` and input it to each one of the models specified. It will also
store the result and try to compile it with `spicyc` to test if the LLM produced a reasonable response or not.
To keep track of each run, a README.md file is created in the output directory. The flag `--note` can be used to add a note to the run.

## Spicy CLI Tools
These tools may be handy when working with spicy to compile and test grammars.
* `spicyc -c <file>`: output C++ code for file
* `spicyc -j <file>`: generate and run parser
* `spicy-build -o output <file>`: generate parser executable
* `spicy-driver <file>`: read from stdin and inputs it to the parser.
  Run with `-d` flag to avoid the compiler from optimizing out fields.
* `spicy-dump <file>`: like `spicy-driver`, but outputs in pretty format.
  With `-J` it outputs JSON.

## Useful extra features (not in INSTRUCTIONS.md)
The following features are not currently included in the `INSTRUCTIONS.md` prompt,
but are written down here for future reference in case they are needed.
* Contexts: allows for use of shared context between parsing parsers. This could
  be used for request-response like protocol messages, which tend to share some
  common values. 
* Filters
* On-the-fly Type Conversion
* Hooks
