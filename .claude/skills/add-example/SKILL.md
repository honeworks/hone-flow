---
name: add-example
description: Add a runnable, explained example to examples/ that the tests execute. Use when a change adds a concept users should see, or the user asks for an example.
---

# Add an example

1. One concept per file: `examples/<name>.py`. Follow the shape of `examples/run_labels.py`:
   - a module docstring with **What** (what it shows), **How** (the calls it uses) and **Why** (the
     problem it solves);
   - offline and fast: a temporary folder as storage, fakes from `hone_flow.testing`, no network, no GPU;
   - prints something that shows the result.
2. List it in `examples/README.md`, in reading order.
3. Add its name to the expected set in `tests/e2e/test_examples.py::test_ac27_examples_are_the_spec_set`.
4. `uv run pytest tests/e2e/test_examples.py -q`.
5. If `design/current.md` §6 lists the examples, add it there too.
