FROM python:3.12-slim

WORKDIR /opt/porthouse

COPY pyproject.toml README.md LICENSE ./
COPY porthouse ./porthouse
COPY evals/suites ./evals/suites
RUN pip install --no-cache-dir .

COPY extensions/capability-document-processing/pyproject.toml /opt/document-processing/
COPY extensions/capability-document-processing/src /opt/document-processing/src
RUN pip install --no-cache-dir /opt/document-processing

USER 65534:65534
WORKDIR /workspace
