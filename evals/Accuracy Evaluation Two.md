# Spec Extract Evaluation

## Evaluation Dimensions
Two dimensions:
- Model: Try to compare frontier models against open source less smarter models, like GPT-OSS, Qwen, etc.
- Number of runs (doesnt make much sense): Probably take this out since it doesnt make sense for smart models and doesnt show anything relevant for smaller models.
- With/Without Negative Inputs: To remove this dimension we will only generate grammars with negative packets.
- Target Language: Both Spicy and 3D, maybe use 3D only on those we know fall in the language expresiveness capabilties.
- Without Spec: Interesting experiment to run with frontier models, interesting to show how it works with simple/complex and niche/popular protocols.
- With Automatic Summarization: Another possible experiment to run on protocols with long specs.

## Experiments:
### Model Comparison:
Choose between 2 to 3 LLM models and run on a subset of all the protocols.
Models chosen:
- GPT-5.4
- GPT-OSS 20B

### Peak Performance Generation
Run with a frontier model among all the protocols collected so far to show the best generation
performance reached. This model will be between GPT-5.4, GPT-5.5 or GPT-5.6 Sol.
The frontier model used during developement was GPT-5.4 with temperature=0.

### Target Language Comparison
Compare expresiveness of target languages generating the same protocols on both, with and without
negative packets (since 3D is probably very limited with neg. packets).
Here it would be interesting to test among these dimensions:
1. Protocol Complexity: Simple ==> Complex
2. Protocol Type: Binary or Text
3. Protocol Constraints: Value Constraints vs. Structural Constraints

### No Spec Generation
Generate grammars for a subset of protocols with no specification provided to see if the LLM
can remember the format of the protocol out of its training data. The two dimensions to check here
are:
1. Protocol Complexity: Simple (UDP) ==> Complex (HTTP1.1)
2. Protocol Popularity: Popular (TCP) ==> Niche (CoAP, SPP)

### Spec Summarization Performance
Generate grammars from automatically summarized specs to show performance difference, if any,
and how summarizing can influence the generation step.
The dimension to check here is Protocol's Spec Length.
