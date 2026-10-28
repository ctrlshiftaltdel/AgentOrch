"""Per-group implementations of the delivery regression scenarios.

The runner (`scripts/run_delivery_scenarios.py`) keeps the registry, the
selector, the executor and the CLI. Each ``group_*`` module owns one group's
fixtures and scenario functions; ``common`` holds what more than one group
needs. The registry order, scenario ids and report schema are unchanged.
"""
