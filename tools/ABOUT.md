# Hourly refresh on GitHub Actions

`.github/workflows/hourly.yml` runs `tools/run_hourly.py` every hour (and on demand).

1. Loads the previous state from the `pipeline-state` branch (the last published bundle and its publication record).
2. Downloads the registered sources directly and extracts figures with code (`vp.py direct`). There is no AI step and nothing to approve.
3. Validates every figure with the same rules as before (`vp.py prepare`): evidence, label, geography, election, definition, observation date, reviews for unexplained changes, no double counting.
4. Exports only allow-listed public datasets and blocks the run if any private text is found (`pubexport.py`).
5. Commits `data/` with the publisher identity and a one-line message, checks the commit metadata, pushes, and confirms the branch now carries this run.
6. Saves the new state, this run's evidence archive and any changed source files to the state branch.

`PUBLISH: dry-run` writes to the `actions-dryrun` branch only. `PUBLISH: live` updates the public site.
