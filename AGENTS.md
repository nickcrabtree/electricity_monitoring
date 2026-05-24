# Agent notes for electricity_monitoring

## Running tests

Use the `electricity` conda environment:

```
conda run -n electricity python -m pytest tests/ -v
```

All Python on quartz uses conda environments — never bare `pip` or `python`.

## Code quality (desloppify)

See [docs/DESLOPPIFY.md](docs/DESLOPPIFY.md) for scores, what was improved, next steps, and the full workflow for running subjective review batches.
