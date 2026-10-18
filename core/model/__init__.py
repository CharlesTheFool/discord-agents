"""
The model layer (0.13.0): what the engines need from a model, independent
of which provider serves it.

- catalog: per-model capabilities, limits and prices
- messages: the persisted message vocabulary
- driver: the request/response protocol every adapter implements
- registry: tool definitions and their per-request assembly
- adapters/: one module per native wire API
"""
