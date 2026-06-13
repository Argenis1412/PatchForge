# Experiment Artifact Layout

> Canonical directory structure for a pipeline run's artifacts.
> Defined in issue #79 (Experiment Artifacts Schema).

## Layout

```
runs/<run_id>/
├── run.json          # RunMetadata (existing — pipeline.py)
├── plan.json         # ArchitectOutput (existing — architect.py)
├── patch.diff        # generated patch (existing — executor.py)
├── validation.json   # ValidatorOutput (existing — validator.py)
├── apply.json        # ApplyOutput (existing — pipeline.py)
├── verdict.json      # Verdict — issue #79
├── verdict.md        # human-readable summary — issue #79
└── issue.md          # human input — written by Issue B (--issue-file)
```

## Notes

### `issue.md`

`issue.md` is written by the `--issue-file` flag on the `plan` command
(ISS-B, issue #92). It contains the original markdown content (frontmatter
+ body) of the human-submitted issue. Absent in runs that used the standard
Scout input path — `issue.md` is optional by design and its absence does
not indicate a failed run.

### `schema_version`

`Verdict` does not carry `schema_version`. ADR-0004 scopes versioning
to `RunMetadata` only. `Verdict` will require `schema_version` when
intermediate schemas are versioned at P3.
