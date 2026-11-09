# Text rewriting on Render

The MCP entry point loads the packaged `service/scripts/clean_strategy.json`
at startup. Its default is `paraphrase@0.5`, without the optional local masked
language model. `WATERMARKS_CLEAN_STRATEGY_FILE` overrides the packaged path.
The Docker image already copies the entire scripts directory.

Rewriting still requires a real model endpoint. In the Render service's
Environment settings, configure:

| Variable | Value |
| --- | --- |
| `WATERMARKS_REWRITE_BACKEND` | `openai-compatible` |
| `WATERMARKS_REWRITE_BASE_URL` | The provider's origin/base prefix, without `/v1` or `/chat/completions` (the client appends `/v1/chat/completions`) |
| `WATERMARKS_REWRITE_MODEL` | An available model identifier from that provider |
| `WATERMARKS_REWRITE_API_KEY` | The provider's key, stored as a secret |
| `WATERMARKS_REWRITE_ALLOW_REMOTE` | `1`, authorizing submission of text to the configured remote provider |

Save the settings and redeploy the existing service. Use `clean_file` with
a short test document and verify the returned text, not just the HTTP status.
Do not commit keys. OAuth credentials for this MCP service are separate from
the rewrite provider's API key. A subscription to a chat application does not
by itself configure a model API for this server.

## Statistical watermark detection

The core image does not bundle the optional MarkLLM model stack. It has a
separate `service/Dockerfile.markllm` image and requires matching generation
configuration. Installing it cannot make detection valid for arbitrary text.

Gumbel requires the original generation key in `WATERMARKS_GUMBEL_KEY`, plus
the matching tokenizer and PRF layout. Do not generate an arbitrary key just
to make the detector report available. That would not validate the target text.

The `claude-text` implementation is a placeholder in this source, not a working
detector. It cannot be enabled by adding a key alone. Treat unavailable
detectors as unavailable, never as negative detection results. Neither rewriting
nor a negative detection result certifies human authorship or removal of all
watermarks.
