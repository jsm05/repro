# Live smoke (2026-11-19T18:46+00:00)

## Route calls

| Route | Model | OK | Latency s | Prompt | Completion | Reasoning | Reasoning at effort=low | Ledger matches | Detail |
|---|---|---|---|---|---|---|---|---|---|
| nvidia/mistral-large-4 | `mistralai/mistral-large-4-1t-a52b` | yes | 7.0 | 22 | 14 | 12 | 38 | yes | stop |
| nvidia/nemotron-3-ultra | `nvidia/nemotron-3-ultra-550b-a55b` | yes | 0.5 | 23 | 14 | 11 | 13 | yes | stop |
| nvidia/laguna-xs-2.1 | `poolside/laguna-xs-2.1` | yes | 0.1 | 49 | 2 | 0 |  | yes | stop |
| gemini/flash-lite | `gemini-3.5-flash-lite` | NO | 0.0 | 0 | 0 | 0 |  | no | transient (HTTP None): APIConnectionError |

## Models visible to each key

- **nvidia** (32): `bigcode/starcoder2-15b`, `google/gemma-3-12b-it`, `google/gemma-3-4b-it`, `google/gemma-4-31b-it`, `meta/muse-glimmer-30b`, `minimaxai/minimax-m3.1-flash`, `mistralai/mistral-large-4-1t-a52b`, `moonshotai/kimi-k3.1-pro`, `nvidia/ai-synthetic-video-detector`, `nvidia/cosmos-reason2-8b`, `nvidia/embed-qa-4`, `nvidia/ising-calibration-1.5-31b`, `nvidia/llama-3.1-nemoguard-8b-topic-control`, `nvidia/llama-3.1-nemotron-safety-guard-8b-v3`, `nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1`, `nvidia/llama-3.2-nv-embedqa-1b-v1`, `nvidia/nemotron-3-embed-1b`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, `nvidia/nemotron-3-ultra-550b-a55b`, `nvidia/nemotron-3.5-bolt-e4b`, `nvidia/nemotron-3.5-content-safety`, `nvidia/nemotron-3.5-embed-1b`, `nvidia/nemotron-3.5-embed-8b`, `nvidia/nemotron-3.5-lightning-30b-a3b`, `nvidia/nemotron-3.5-super-vl-120b-a12b`, `nvidia/nv-embedqa-mistral-7b-v2`, `nvidia/nvclip`, `nvidia/riva-translate-4b-instruct-v2`, `poolside/laguna-xs-2.1`, `snowflake/arctic-embed-l`, `stepfun-ai/step-5-preview`, `z-ai/glm-5.3-flash`
- **gemini**: model list failed: ValueError: Invalid header value b'Bearer AQ.Ab8RN6LUYslpdGXuqHqDyfOlJdL_jcz6d0AV5m33Jqpfcnd8IQ\n'
- **openrouter**: model list failed: ValueError: Invalid header value b'Bearer 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\n'
