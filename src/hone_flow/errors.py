"""Typed errors raised at the public API."""

from __future__ import annotations


class HoneFlowError(Exception):
    """Base class for every error hone-flow raises on purpose."""


class WorkflowDefinitionError(HoneFlowError):
    """The workflow's steps cannot be wired together (unknown parameter, cycle, duplicate name, ...)."""


class StepFailed(HoneFlowError):
    """A step raised and the run was started with ``fail_fast=True``."""

    def __init__(self, step: str, item: str | None, traceback: str, *, run_id: str = "") -> None:
        super().__init__(f"step {step!r} failed for item {item!r} in run {run_id!r}:\n{traceback}")
        self.step = step
        self.item = item
        self.traceback = traceback
        self.run_id = run_id


class IncompatibleRun(HoneFlowError):
    """``resume()`` would mix step versions (or the workflow changed); fork the run instead."""


class RunLocked(HoneFlowError):
    """Another live process holds the run's lease."""


class RunNotFound(HoneFlowError):
    """No run folder with that id under ``runs/`` or ``pinned_runs/``."""


class OutputNotFound(HoneFlowError):
    """The step has no current output for that item (not run, failed, or retired)."""


class ReviewError(HoneFlowError):
    """A review decision on a step that is not a gate, or not awaiting review."""


class WriteConflict(HoneFlowError):
    """A conditional write failed: the key changed since it was read, or exists when it should not."""
