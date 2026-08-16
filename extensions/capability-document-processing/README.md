# Porthouse Document Processing Capability

Optional, business-neutral extraction of immutable private Runtime Input Assets.

The `document.extract` capability accepts one Input Asset already frozen into the
current Run. It executes PDF/DOCX parsing in a bounded Python subprocess by default
and returns a private, versioned Runtime Artifact containing bounded text chunks and
page/offset evidence. The model-facing result contains only Artifact identity and
parse metadata. Parsing never runs in the Runtime Worker process.

This capability does not index Knowledge, interpret business fields, or expose
the original binary. Products perform their own schema extraction in a later Run.

The default `subprocess` backend is intended for local and personal deployments. It
uses a fixed module command (no shell), an isolated temporary directory, a scrubbed
environment, wall/CPU/file/descriptor limits and process-group termination. Linux
also applies an address-space limit. The trusted parser implementation performs no
network calls, but this backend is not a kernel network sandbox.

Production deployments can select the stronger network-disabled `container` backend:

```json
{
  "isolation_backend": "container",
  "container_image": "porthouse-document-processing:0.1.0"
}
```

Build the optional parser image from the repository root:

```bash
docker build \
  -f extensions/capability-document-processing/Dockerfile \
  -t porthouse-document-processing:0.1.0 \
  .
```

When `container` is selected, an unavailable sandbox fails closed and never falls
back to `subprocess`. When `subprocess` is selected on an unsupported host, it also
fails closed. `in_process` is intentionally not a supported backend.
