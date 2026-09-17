"""Adapters to the services the pipeline calls.

Each adapter hides one provider behind a small interface, so that swapping the engine —
for instance replacing Jev with a local ranking model — touches this package only.
"""
