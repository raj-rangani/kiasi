# Developing Kiasi

```
python3 -m unittest discover -s tests   # hook handlers, reports, dashboard
claude plugin test .                     # hooks/*.test.ts on the real engine
claude plugin validate .                 # manifest, marketplace and hooks.json
```

`scripts/kiasi.py` is the hook entry point and dispatches to `scripts/core/`. Reports live in `scripts/reports/`, the dashboard in `dashboard/`, and `scripts/README.md` says which hook calls what.
