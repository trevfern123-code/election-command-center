# Hourly runner: observation file format (toolkit v4)

Write `obs.json` with these keys. Report only what pages say. Never estimate, and never carry a number from memory, the bundle or a previous run into a figure. The script validates every figure **as a unit**: the number, its label, the place, the election and the observation date must all appear in the verbatim text you save. If any part is missing, the figure is rejected, and the last verified figure stays on the dashboard, marked as such.

```
{
  "started_at": "<ISO UTC when the run began (STARTED)>",
  "attempts": [            // one entry per fetch attempt, including retries and skipped groups
    {"source_id": "tpsi", "url": "...", "attempt": 1,
     "status": "ok | no_data | unreachable | blocked | error | timeout | skipped",
     "started_at": "<ISO UTC just before the fetch>", "ended_at": "<ISO UTC just after>",
     "rows_stated": 43,            // table pages only: how many jurisdictions the page itself says it covers (e.g. "across 43 reporting states"); omit if not stated
     "detail": "short note"}
  ],
  "figures": [             // one per measure per source
    {"measure": "NC.early_total",          // from plan.groups[].measures
     "source_id": "ncsbe",
     "value": 1101321,                     // the number as printed (744K -> 744000 with precision "rounded")
     "mail": null, "in_person": null,      // only when the page prints both parts
     "precision": "exact | rounded",       // rounded when the page prints K/M, "about", "more than", ...
     "data_as_of": "2026-10-21",           // the period the data covers, as the source states it (NOT the fetch time)
     "election": "<verbatim words naming the election, e.g. '2026 General Election' or 'Election Date: 11/3/2026'>",
     "url": "<exact page or file URL read>",
     "evidence": "<verbatim text containing the number; for multi-state pages it must also contain the place name>",
     "metric_label": "<verbatim label of the number, e.g. 'Total Ballots Cast'>",
     "label_evidence": "<verbatim text containing that label, e.g. the table header row 'State | Ballots | Mail | In person'>",
     "as_of_evidence": "<verbatim text containing the date, e.g. 'Ballots cast through 10/21/2026' or 'As of October 22, 2026'>",
     "is_correction": false, "correction_note": null,   // true only when the source itself says it revised a figure
     "calc": null,                         // see "Calculations" below
     "compare_2022": {"value": 798778, "evidence": "<verbatim>"}   // optional; only when the same report prints the 2022 figure
    }
  ],
  "registration": [ {"abbr": "GA", "registered": 7000000, "registration_type": "active | total | active+inactive",
                     "as_of": "2026-10-06", "source_name": "...", "source_url": "...", "evidence": "<verbatim>"} ],
  "ratings":   [ {"race_id": "S-NC", "rating": "Lean D", "date": "2026-10-23", "source_url": "...", "evidence": "<verbatim>"} ],
  "polls":     [ {"race_id": "G-AZ", "pollster": "...", "sponsor": "...", "sponsor_type": "independent|interest group|campaign|party",
                  "field_start": "YYYY-MM-DD", "field_end": "YYYY-MM-DD", "sample_size": 800, "population": "LV|RV|A", "method": "...",
                  "moe": 3.5, "d_name": "...", "d_pct": 48, "r_name": "...", "r_pct": 45, "other_text": null,
                  "url": "<original release if possible>", "verified_from": "original|news report|aggregator listing", "note": null} ],
  "headlines": [ {"scope": "National|AZ|<state abbr>", "title": "...", "outlet": "...", "url": "...", "date": "YYYY-MM-DD", "summary": "one neutral line", "event_id": "<same id for one event>"} ],
  "briefing":  [ {"category": "Election administration|Watched race|Results|Correction", "what_changed": "sourced fact", "why_it_matters": "one line",
                  "source_name": "...", "source_url": "...", "observed_at": "YYYY-MM-DD", "importance": 1} ],
  "results":   [ {"id": "<watch race id or AZ-...>", "status": "Leading in reported results|Projected winner|Too close to call|Recount|Certified|Awaiting results",
                  "cand_d": "...", "votes_d": 0, "cand_r": "...", "votes_r": 0, "other_votes": 0, "reported_at": "ISO", "latest_batch": "...",
                  "source_name": "...", "source_url": "official results page", "outstanding": "only as officials state it",
                  "expected_vote_pct": null, "expected_vote_provider": null, "next_update": "only if announced",
                  "projection_by": "AP|...", "certification_status": "...", "is_correction": false} ]
}
```

## Attempt statuses
- `ok`: the page was read and had the data.
- `no_data`: the page loaded, but has no figure for this election or nothing newer. This is **not** a failure.
- `unreachable` / `blocked` / `error`: the page could not be read. Retry once, then stop.
- `timeout`: the fetch took more than 90 seconds, or the source's attempts took more than 180 seconds in total. Do not retry it.
- `skipped`: `vp.py clock` said to stop fetching before this group was reached.

## Dates: data period vs publication time
- `as_of_evidence` must be the page's own words about the date. If the page states the period the data covers ("through 10/21/2026", "as of October 22", "so far"), copy that.
- If it gives only a publication or update time ("Last updated …", "Report generated …", "Published …"), copy that text exactly. The script then treats the date as a publication time. Such a date never justifies switching to that source as "newer".
- Never write a fetch time or an article's publication date as `data_as_of`, unless the article itself says the figure is "as of" that time.
- If the election name has no year (e.g. a press-release headline), the year must appear in `as_of_evidence` or `evidence`. Otherwise the figure is rejected.

## Tracker page dates (TPSI, On Point, Call the Map, NBC)
- These pages print one date for the whole page ("as of October 23"). Each state inside reports on its own schedule, so that date is not the state's observation date.
- The script treats these dates as page dates. A page-dated tracker figure never replaces a figure from an official source. It is shown alongside the official figure as "tracker, not confirmed". It is published only for states with no official source.
- Never record party numbers from trackers. They are modeled estimates or registration splits, not counts. Figures labeled "model", "estimate", "projected" or "expected" are rejected.
- On Point state pages for states that do not report say "<State> has not reported any 2026 early ballots yet". Record that attempt as `"status": "no_data", "coverage": "not_reporting", "measure": "<ST>.early_total"` with no figure.
- These pages also show the nationwide headline ("ballots cast so far"). Never record it as a state figure.

## Table rows
A page may show a table with a header such as `State | Ballots | Mail | In person`. For each state, report:
- `evidence`: that state's row, e.g. `California | 2,326,426 | 2,326,426 | 0`.
- `metric_label`: the column name, e.g. `Ballots`.
- `label_evidence`: the header row.
- `as_of_evidence`: the page's date line, e.g. `As of October 22, 2026`.

## Calculations
When the source prints only parts of a total, give the total as a labeled calculation. Florida, for example, prints Voted Vote-by-Mail and Voted Early separately:
```
"value": 1682655, "precision": "exact",
"calc": {"op": "sum", "parts": [
   {"label": "Voted Vote-by-Mail", "value": 1130457, "evidence": "Voted Vote-by-Mail | ... | 1,130,457"},
   {"label": "Voted Early", "value": 552198, "evidence": "Voted Early | ... | 552,198"}]}
```
The script checks that each part and its label appear in the saved text and that the parts add up. The dashboard labels the figure "Calculated".

## What the script does with figures
It checks each figure for the election, place, label, metric definition, observation date, verbatim number, registered host and plausibility, and it removes duplicates.

**Number formatting.** Differences such as 1,234,567 / 1234567 / 1 234 567 / 1.27M are handled. A rounded figure is never accepted as an exact count.

**Unexplained changes.** These are never silently dropped. They go **under review**: the published figure stays, both values are kept and shown, and the next hourly read re-checks them. Unexplained changes include:
- a decrease
- a different value for the same period
- a jump of more than 3x
- a figure above active registration

If the same value is read in two consecutive hourly runs, the decrease, same-period change or jump is adopted and logged as a revision. A figure above registration stays under review.

**Rounded figures** for measures that require exact counts are kept for source comparison only.

**Backups** are chosen by data period, definition and coverage. A backup replaces the published figure only when all of these hold:
- its data period (not a publication time) is later
- it counts the same thing
- it covers the same area

A backup with a different definition is used only when the published figure is stale, and it is labeled.

**Tables.** For a table page, record `rows_stated` and return every row. If the page itself delivers only part of the table (for example TPSI's HTML has 20 of 43 rows; the rest load by script), record what was returned; do not invent rows. The script compares the unique rows returned with the number the page states, and lists missing rows and duplicates. An incomplete table makes the run "degraded".

## v9 additions
- **Fresh reads (historical, v9-v11; since v12 no group is given to the web reader).** The plan gives each web-reader group `fetch_urls` with a per-run `_ecc=` parameter. The web reader can return an old cached copy of a page; on 2026-10-23 it returned Minnesota's 10/16 page while the live page showed 10/23. Fetch `fetch_urls`. Only if that fails, fetch the plain URL and add `"cache_bust": false` to the attempt. Record the plain URL in figures.
- **Minnesota (official page).** Record the two printed parts, "Accepted ballots - by mail (MM/DD/YY)" and "Accepted ballots - in person (MM/DD/YY)", as a calc. `data_as_of` is the date printed in those labels. News reports may give a different cutoff date. Each source keeps its own date; never substitute one date for the other.
- **DC.** Record the drop-box TOTAL plus the USPS TOTAL as a calc. `data_as_of` is the last date with data in both tables. Ignore the dates in the file names.
- **Coverage gaps.** A jurisdiction with no figure shows a researched reason. A tracker state page counts as a fallback only when it actually prints a figure for that state.

## v10 additions
- **Older answers.** If a page shows data older than the dashboard's published figure (for example, a cached official page still dated 10/16), record the figure with its own older date. Do not record no_data. The script keeps the published figure and its provenance, and labels the row "stale source response; freshness not verified this run".
- **Tables with dated rows (DC).** Each calc part can carry "heading" (the table heading verbatim) and "rows" (every dated row with its TOTAL value and verbatim evidence). The script checks that the heading is in the saved text, that the rows add up to the printed TOTAL, and that data_as_of is the last day reported in every table.
- **Same-day tracker updates.** A tracker that raises a figure under the same page date is accepted automatically when its own daily series records the change on that day and the rise is under 25%. Decreases and same-period changes from official sources still go to review and are adopted after two consecutive hourly reads.

## v10.1 (2026-10-23.11): no return to an older official observation, and self-repair

- Live run r20261023T2216 (toolkit 2026-10-23.10) replaced Minnesota's tracker count (419,189, page date Oct 23) with the official page's Oct 16 figure (202,252), because the row had no tracker daily-series date. A page-dated tracker count is now compared with the official report's data date using the page date when no daily-series date exists: an official report for an earlier day never replaces it and is shown as a "stale source response; freshness not verified this run". An official report dated the same day or later still takes over.
- Self-repair: when the latest source change for a figure moved it to an OLDER observation, and the source it replaced now reports a figure at least as recent and at least as large as both the replaced figure and the figure now published, the figure returns to that source (logged as a source change with the reason). This repairs Minnesota and Georgia on the next run without any manual edit. It does not apply when the published source has since moved ahead (Texas: TexPolls through Oct 22 is larger than the tracker count it replaced).
- Same-day changes adopted after two matching reads are labeled "same-day change confirmed on two matching reads" instead of "correction".

## v12 (2026-10-24.12): no approval prompts in unattended runs

- **Rule: scheduled runs never call WebFetch.** The plan lists only direct-download results and web-search groups (`"how": "websearch"`).

- From the 02:06Z run on Oct 24, every web-reader (WebFetch) request in the scheduled task was refused: the reader asks a person to approve each URL that did not come from the person, and nobody can answer in an unattended run. The refusals were recorded as errors and reported as approval requests to the owner.
- Moved to direct download (code extraction, raw file hash kept): Call the Map (`ctm_states`: exact state counts, each dated by the day it runs through; sections covering only part of a state are not recorded; the national total is dated by the earliest day in its range) and the AZ ABEV Returns newsletter (`substack_abev`: its RSS feed; the date comes from the post title "AZ ABEV Returns - MM/DD/YYYY").
- Sources that refuse automated downloads (HTTP 403 or a bot challenge from the site itself), sources whose host the environment's network allowlist blocks, and pages that load their content only by script cannot be read without the web reader. Groups listed in the registry as `"unattended": "blocked"` are never given to the web reader; every run records them as `permission_blocked` with the reason, keeps the last verified figure with its date, and lists them separately (`sources_permission_blocked`). They never count as "no new data".
- obs.json attempts may use status `permission_blocked`. Any attempt whose error says a permission request was not answered (or PROVENANCE_REQUIRED) is classified as `permission_blocked` automatically.
- Run mode: a run is `unattended` unless obs.json reports `"approvals_answered": n > 0` (or an attempt with `"approval": "granted"`), in which case it is `assisted`.
- Replaced dataset files are deleted one run later (`retired_blobs` in pipeline/latest; `vp.py batch --prev-latest`), so a page opened before a publication does not lose its data files.
