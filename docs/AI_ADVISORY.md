# AI Advisory

AI is optional and advisory only. It cannot create, veto, promote, or downgrade a canonical decision.

The maintained advisory path receives the canonical evidence packet, including derivatives/flow/cascade/cross-exchange context. Failure or missing credentials produces `UNAVAILABLE`; deterministic logic continues.

The canonical configured provider is Gemini when `GEMINI_API_KEY` is present. No local Ollama fallback is required by the current production architecture. An AI runtime/model must not be kept on the host unless active code references it and a maintained health path exists.

Never place credentials or raw provider responses containing secrets in logs, Git, or handoff certificates.

## Local-model runtime status

The canonical production tree has no Ollama runtime dependency. Historical WaterfallHunter Ollama containers/volumes are cleanup-eligible only after release certification. A generic `ollama/ollama` image is removed only after a host-wide container dependency check proves no non-WaterfallHunter workload references it.
