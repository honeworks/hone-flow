# Steps across items

Most steps run once per item. Some work needs **every** item's result: a workbook made from all lessons,
a channel page listing every episode. This page shows the steps that look across items.

Examples: [`fan_in.py`](../examples/fan_in.py), [`produced_items.py`](../examples/produced_items.py),
[`select_items.py`](../examples/select_items.py).

## Final steps: after all items (fan-in)

`@wf.final_step()` registers a step that runs **once, after the items**. A parameter named after an item
step (or one of its `outputs=` names) receives a `dict` from item id to that step's output, in item
order; a global or another final step's output arrives as it is. A final step may also take
`fk.Param[...]` and `fk.Context`, but not item inputs or `fk.Item` (it belongs to no item), and item steps
cannot take a final step's output.

```python
import tempfile

import hone_flow as fk

wf = fk.Workflow("course", storage=tempfile.mkdtemp())


@wf.step()
def summary(topic: str) -> str:
    return f"what {topic} means"


@wf.final_step()
def workbook(summary: dict[str, str]) -> str:
    return "\n".join(f"{lesson}: {text}" for lesson, text in summary.items())


run = wf.run([fk.Item("01", {"topic": "listening"}), fk.Item("02", {"topic": "feedback"})])
assert run.output("workbook") == "01: what listening means\n02: what feedback means"
assert run.steps("workbook")[0].kind == "final"
```

- **Where it lives.** Like a global step: `<step>/` in the run folder, `item` is `None` in its records.
  Each item's input is snapshotted as `inputs/<parameter>/<item id>/<file>`, and the input's `from` is
  `items:<step>` ([run format](run-format.md#metadatajson-one-per-step-folder)).
- **When it runs.** After every item it needs is `done`. If one of them `failed` (or is `blocked`) the
  final step is `blocked`; if items were left out by the call (`items_filter`, `resume(items=...)`) it is
  `skipped` and a later `resume()` runs it; while an item waits at a gate it stays `pending`.
  `@wf.final_step(partial_ok=True)` runs anyway with the items that are done.
- **Order.** Breadth-first, final steps come after every item step they depend on. Depth-first takes each
  item through the steps before the final step, then runs it.
- **Fork.** Refreshing one item's step reruns that item's downstream and every final step that consumes
  it; a fork with other items (`items=[...]`) reruns final steps with the reason `items_changed`.
- **Reject.** Rejecting a producer at a gate also sends back every final step downstream of it (attempt
  status `replaced`); `resume()` reruns it with the revised output.

```python
lesson = run.fork(refresh={"summary": ["02"]}, dry_run=True)
reasons = {(row.step, row.item): row.reason for row in lesson.rows}
assert reasons[("summary", "01")] == "unchanged"
assert reasons[("workbook", None)] == "downstream_of:summary"
```

`refresh={"summary": ["02"]}` (a mapping from step to item ids) refreshes a step for some items only;
see [resume and fork](resume-and-fork.md#fork).

## Items made by a step (fan-out)

Sometimes the items are only known once a step has run: an outline decides how many chapters an episode
has. A global step annotated to return `fk.Items` makes them, and steps declared `per="<that step>"` run
once per produced item, receiving the item's inputs like any item step (`fk.Item`, or parameters named
after its inputs).

```python
episode = fk.Workflow("episode", storage=tempfile.mkdtemp())


@episode.global_step()
def outline(concept: fk.Param[str]) -> fk.Items:
    return fk.Items(fk.Item(f"ch{n}", {"title": t}) for n, t in enumerate(["why", "how"], 1))


@episode.step(per="outline")
def narrate(title: str) -> str:
    return f"narration about {title}"


@episode.final_step()
def cut(narrate: dict[str, str]) -> str:
    return " / ".join(narrate.values())


show = episode.run([], params={"concept": "tides"})  # no items of its own: the outline makes them
assert [(i["id"], i["items_from"]) for i in show.manifest["items"]] == [
    ("ch1", "outline"),
    ("ch2", "outline"),
]
assert show.output("cut") == "narration about why / narration about how"
```

- **Where they live.** Produced items join the manifest's `items` with `items_from: "<step>"` when the
  producer commits; their step folders are `<step>/item_<id>/` as usual. The producer's own output
  (`output/<step>.json`) lists them, and `run.output("outline")` loads it back as `fk.Items`. Produced items
  take JSON inputs (files travel through step outputs); ids must be new in the run.
- **Rules.** `per=` must name a `@wf.global_step` annotated `-> fk.Items` (`wf.validate()` checks it). An
  item step over produced items only takes item steps over the same items; global steps' outputs, params
  and the context as usual. `@wf.gate(per=...)` reviews produced items too. Final and select steps take
  steps over produced items as dicts, and wait until the producer is done.
- **Order.** The producer runs before its items' steps (breadth-first: all narration, then all pictures,
  so each GPU model loads once). `until=` includes the producer.
- **Resume.** A crash in the producer reruns it before any produced item. Once items exist,
  `resume(items=[...])` can name them; `items_filter` / `items` select produced items like any others.
- **Fork.** Produced items are compared by id and inputs: when the producer reruns, an item with the same
  inputs keeps its copied results, an item whose inputs changed reruns its steps (plan reason
  `produced_item_changed`), a new one runs (`new_item`) and one no longer produced is dropped; final steps
  over them rerun. These rows are decided when the producer has run, so a dry run cannot show them.

## Select steps: choosing which items continue

Sometimes the choice sits in the middle: score 45 job posts, keep the best 10, write drafts only for
those. `@wf.select_step()` sees every item's output like a final step and returns the item ids that go on,
as a list or as `fk.Selection(keep=[...], reasons={item_id: why})`. Its output is stored as an
`fk.Selection` at `<step>/` (like a global step).

Item steps that take the select step's output (a parameter named after it, receiving the `fk.Selection`)
run only for the kept items. For the others they become **`not_selected`**, and so does everything
downstream of them for those items: a final state that counts as done, so the run can be `completed`.
Why an item was left out is in the selection's `reasons`.

```python
picks = fk.Workflow("leads", storage=tempfile.mkdtemp())


@picks.step()
def fit(score: int) -> int:
    return score


@picks.select_step()
def shortlist(fit: dict[str, int]) -> fk.Selection:
    keep = sorted(fit, key=lambda post: -fit[post])[:2]
    return fk.Selection(keep=keep, reasons={p: "lower fit" for p in fit if p not in keep})


@picks.step()
def draft(fit: int, shortlist: fk.Selection) -> str:
    return f"a draft for a fit of {fit}"


day = picks.run([fk.Item(p, {"score": s}) for p, s in [("p1", 3), ("p2", 9), ("p3", 7)]])
assert day.status == "completed"
assert day.manifest["state"]["draft/p1"] == "not_selected"
assert day.output("shortlist").reasons == {"p1": "lower fit"}
```

- **When it runs.** Like a final step: after every item it needs, `blocked` when one failed, unless
  `@wf.select_step(partial_ok=True)` (then it chooses among the items that are done; an item whose own
  steps failed stays `blocked`, not `not_selected`).
- **Ids.** The kept ids must be items of the run; another id fails the step.
- **Resume.** `not_selected` is final: `resume()` leaves it, and `resume(items=[...])` with a
  not-selected item raises `fk.HoneFlowError` (fork with those items to run them).
- **Fork.** An unchanged selection is reused, and its left-out items stay `not_selected` (plan reason
  `not_selected`). When the select step reruns (its inputs, params or items changed), the steps that take
  its output rerun for every kept item.
- **Reject.** Rejecting a producer upstream of a select step sends the selection back too; its
  `not_selected` items become `pending` and the selection is made again on `resume()`.
- **Final steps** after a select step see only the selected items.
