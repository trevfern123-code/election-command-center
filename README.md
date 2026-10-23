# 2026 Election Command Center (public, read-only)

A nonpartisan dashboard for the U.S. general election on November 3, 2026: early votes by state, battleground races,
control of Congress, turnout pace against 2022, Arizona statewide races and measures, a Gilbert-area (Maricopa County, Arizona)
ballot guide, and election-night results.

**How it updates.** An automated hourly process collects figures from the linked sources, validates each one (number, label,
place, election and the source's own "data as of" date) and publishes only verified results. After each verified publication it
commits the public data files in `data/` to this repository. `data/manifest.json` records when that publication happened and how
complete the source coverage was. The page re-reads the manifest every few minutes.

**Reading the figures.** Every figure shows its source, the date the source says the data covers ("data as of") and when it was
last checked. An hourly check does not mean a source published new numbers. Failed checks, partial coverage and stale figures are
labeled on the page. Nothing here is a forecast. Ballots cast are not votes for any candidate.

**Local ballot guide.** Contests are labeled by the jurisdiction that votes on them (state, county, municipality, district). The
exact ballot depends on a voter's street address; check the official sample ballot at BeBallotReady.Vote.

This site has no accounts, forms or tracking, and it stores nothing about visitors.
