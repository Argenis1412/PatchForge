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

`issue.md` is not written by the pipeline until Issue B implements
`--issue-file`. Its absence does not indicate a failed run. A run
directory without `issue.md` is valid for the POC.

### `schema_version`

`Verdict` does not carry `schema_version`. ADR-0004 scopes versioning
to `RunMetadata` only. `Verdict` will require `schema_version` when
intermediate schemas are versioned at P3.
