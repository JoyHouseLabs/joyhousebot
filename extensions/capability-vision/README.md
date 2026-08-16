# Porthouse Vision Capability

`capability-vision` sends one **frozen Runtime Input Asset** to a configured
OpenAI-compatible vision endpoint for OCR or visual understanding. It cannot
read a local path, an arbitrary URL, or another Run's attachment.

The extension is disabled until it is explicitly installed, deployment-allowed,
published, Worker-ACKed, enabled and granted `context.read` plus `vision.read`.
If its endpoint, model, credential environment variable, or frozen image asset is
unavailable, it fails closed.

## Capability contract

```text
vision.understand@0.1.0
input:  {asset_id, task: "ocr" | "understand"}
output: {observations[], model, asset_id}
```

Every observation has an asset-bound evidence object and a bounded confidence.
The raw image and provider response are not copied to capability output; Runtime
stores the normal private Run/Trace evidence. Provider request bodies are never
logged by this extension.

## Configuration

Set non-secret configuration in the Console capability settings and store the
credential only in the Worker environment:

```json
{
  "api_url": "https://api.openai.com/v1/chat/completions",
  "model": "gpt-4.1-mini",
  "api_key_env": "OPENAI_API_KEY",
  "max_asset_bytes": 5242880
}
```

```bash
export OPENAI_API_KEY='...'
```

The endpoint must be HTTPS, is checked with the SDK's SSRF-protected transport,
and may not be supplied by an Agent request. A provider with a different wire
protocol should be its own Extension, while preserving this frozen-asset and
evidence contract.
