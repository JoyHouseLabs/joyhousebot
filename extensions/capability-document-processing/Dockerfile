FROM python:3.12-slim

WORKDIR /opt/joyhousebot

COPY pyproject.toml README.md LICENSE ./
COPY joyhousebot ./joyhousebot
COPY evals/suites ./evals/suites
RUN pip install --no-cache-dir .

COPY extensions/capability-document-processing/pyproject.toml /opt/document-processing/
COPY extensions/capability-document-processing/src /opt/document-processing/src
RUN pip install --no-cache-dir /opt/document-processing

USER 65534:65534
WORKDIR /workspace
