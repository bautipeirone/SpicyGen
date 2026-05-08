# Architecture Overview

The grammar generator uses an autonomous LLM agent loop to iteratively write, compile, and test Spicy grammars for network protocols based on their RFC specifications.

## The Agent Loop
The core of the system is in `src/agent.py`, which leverages the AutoGen framework to facilitate a conversation between two agents:
- **SpicyDeveloper (`AssistantAgent`)**: Represents the LLM (like GPT-4, Qwen, or Gemini). It is prompted to generate Spicy grammar for a given protocol specification.
- **CompilerProxy (`UserProxyAgent`)**: Acts on behalf of the user to execute tools and provide feedback to the `SpicyDeveloper` without human intervention.

## Tools Available to the LLM
The `SpicyDeveloper` is equipped with specific tools to validate and improve the grammars it generates:
1. **`compile_grammar`**: Compiles the generated `.spicy` code using the Spicy compiler (`spicyc`). If compilation fails, the exact compiler errors are returned to the LLM so it can correct the code in the next turn.
2. **`parse_packets`**: If the grammar compiles successfully, this tool is used to verify the grammar's semantic correctness by running it against a set of known accept/reject packets for the target protocol. The LLM receives detailed runtime logs if the parsing fails.
3. **`retrieve_rfc_by_id`**: Allows the LLM to fetch related RFC documents on-the-fly to clarify dependencies or specific types (e.g., retrieving RFC 1035 for DNS).

## Workflow
1. The script (`generate_grammar.py`) initializes the system prompt and the initial protocol specification.
2. The `SpicyDeveloper` writes a draft grammar.
3. It uses `compile_grammar`. If it fails, the agent reads the errors and tries again.
4. Once compilation succeeds, it uses `parse_packets`. If any packets fail to parse correctly, the agent receives feedback and goes back to adjusting the code.
5. This refinement loop continues until all packets pass (success), or the `max_retries` limit (configured in `config.yaml`) is reached.
6. The system returns the best performing grammar code and comprehensive metrics about the run.
