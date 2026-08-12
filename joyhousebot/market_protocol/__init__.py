"""Portable App Market protocol primitives.

The submodules deliberately contain no database, HTTP server, Runtime, or
payment implementation. Runtime instances and independent Market services
share only deterministic formats and signature rules. Imports stay explicit
to keep the protocol layer free of package-initialization cycles.
"""
