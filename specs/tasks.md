# Tasks: Berlin Hausverwaltung Lead Engine

Status: **Approved 2026-11-08.** Implementation in progress.

`[YOU]` marks a task that needs your input. After each task: run tests, tick the box, send a 2-3 line summary. Before any external API run, show the request count and confirm it stays in the free tier.

Suggested timing: Sunday, sections 1-4. Monday, sections 5-8. Tuesday morning, sections 9-12.

## 0. Approval

- [x] 0.1 `[YOU]` Approve the spec and answer Q1-Q5 in `design.md`. **Done when:** answers are recorded in `design.md`.

## 1. Foundation

- [x] 1.1 Create virtual environment and `requirements.txt`; confirm `geopandas` and `folium` install. (R11.1) **Done when:** both import; if not on 3.14, a Python 3.12 env is documented.
- [x] 1.2 Create the folder structure, `pyproject.toml`, `.gitignore`, `.env.example`, `git init`. (R11.5) **Done when:** `pip install -e .` works and `data/` is ignored.
- [x] 1.3 Config loading and `config/` files, including `cities/berlin.yaml`. (R11.2) **Done when:** a test loads Berlin config.
- [x] 1.4 CLI skeleton with stage selection. (R11.2, R11.3) **Done when:** `python -m leadengine run --city Berlin --stages collect` dispatches.
- [x] 1.5 Request ledger with per-stage logging. (R11.4, R1.5) **Done when:** a test shows it blocks at budget and persists across runs.
- [x] 1.6 Cached, robots-aware HTTP client. (R2.2-R2.4) **Done when:** fixture tests cover cache hit and robots block.

## 2. Collect

- [x] 2.1 OSM source with Overpass. Show the 1-2 query count first. (R1.1, R1.2) **Done when:** real results are cached and the count is reported.
- [x] 2.2 Refactor the Google script to `sources/google_places.py`, keeping tiling and dedup logic; add guard, centre-out order, modes and clean skip. Needs Q1. (R1.3-R1.6) **Done when:** tests pass with mocked responses and no live call is made.
- [x] 2.3 Normalisation, dedup and platform domain denylist. (R1.9, R1.10) **Done when:** dedup tests pass.
- [x] 2.4 Non-target filter with reasons. (R1.11) **Done when:** fixture tests pass and excluded rows are written.
- [x] 2.5 `[YOU]` If using Google: confirm Text Search Enterprise usage is 0 this month and set a daily quota. **Done when:** you confirm.
- [x] 2.6 `[YOU]` Google dry run: 1 request, 1 tile, 10 companies. **Done when:** ledger shows 1 request and you have seen the output.
- [x] 2.7 `[YOU]` Full Google run, after showing the projected count and getting your go-ahead. **Done when:** ledger is at or below 900, then you set the quota to 0.
- [x] 2.8 Merge sources and report unique company count against G1. (R1.8) **Done when:** `candidates.csv` and `excluded.csv` exist with counts.

## 3. Crawl

- [x] 3.1 Page discovery and fetch. (R2.1, R2.3) **Done when:** fixture tests find Impressum and relevant pages.
- [x] 3.2 Text cleaning and 6,000-character trim. (R2.5) **Done when:** fixture produces the expected `.md`.
- [x] 3.3 Dry run on 10 companies, then full crawl. (R2) **Done when:** `crawl_status` is set for every company.

## 4. Rules

- [x] 4.1 Impressum extraction: name, address, managers, legal form, HR number. (R3.1) **Done when:** tests pass on saved HTML fixtures.
- [x] 4.2 Email and phone extraction, email classification. (R3.1, R6.3) **Done when:** tests pass.
- [x] 4.3 Keyword detection with `not_mentioned`. (R3.2, R3.4) **Done when:** tests pass and JSON has `source_url` and matched text.
- [x] 4.4 Run rules on all companies and purge Google files under `discovery_only`. (R1.6, R3.3) **Done when:** one JSON per crawled company; purge is confirmed.

## 5. Claude Code extraction

- [x] 5.1 Pydantic schema, evidence-quote check and validator script. (R4.2, R4.3) **Done when:** schema tests pass.
- [x] 5.2 Backend interface, priority queue, progress file. (R4.4-R4.6) **Done when:** `llm next-batch` lists 20 unfinished companies.
- [x] 5.3 Pilot batch of 20. **Done when:** all pass validation and you have reviewed a few.
- [x] 5.4 Remaining batches until complete or usage limit. (R4.1) **Done when:** `_progress.md` is current and all files validate.

## 6. Geo (GIS)

- [x] 6.1 Download and cache district polygons. (R5.1) **Done when:** the file has 12 districts.
- [x] 6.2 Nominatim geocoder with cache. Show the request count first. (R5.3) **Done when:** a mocked test confirms 1 request per second and cache use.
- [x] 6.3 Spatial join, district assignment, boundary exclusion. (R5.2, R5.4-R5.6) **Done when:** tests pass on points inside, outside and on 12529 Schönefeld.
- [x] 6.4 Run the geo stage. **Done when:** located, unlocated and excluded counts are reported.

## 7. Contacts

- [x] 7.1 Role ranking and person selection. (R6.1, R6.2) **Done when:** tests pass.
- [x] 7.2 Email status and pattern inference; LinkedIn search URL. Needs Q3. (R6.3-R6.5) **Done when:** tests cover all 4 statuses and contact rate is reported against G5.

## 8. Score

- [x] 8.1 `scoring.yaml` and engine. (R7.1-R7.3) **Done when:** tests cover neutral values and completeness.
- [x] 8.2 Reason and first-contact angle templates. (R7.4) **Done when:** tests show reasons built from rule labels only.
- [x] 8.3 Run scoring. **Done when:** every row has score, tier and reason, and the tier distribution is reported.

## 9. Validate

- [x] 9.1 Automatic checks. (R8.1) **Done when:** `qa_flags` is filled and summarised.
- [x] 9.2 Generate the 30-row sample. (R8.2) **Done when:** the same seed gives the same rows.
- [ ] 9.3 `[YOU]` Fill in `validation_sample.xlsx` (about 45-60 minutes). **Done when:** file is saved.
- [x] 9.4 Accuracy report. (R8.3) **Done when:** `validation_report.md` shows per-field accuracy against G2.

## 10. Export

- [x] 10.1 Workbook with 6 tabs and CSVs. (R9.1-R9.4) **Done when:** the file opens and tab checks pass.
- [x] 10.2 `[YOU]` Fill `linkedin_profile_url` for Tier A leads, then re-export. **Done when:** complete, or you skip it.
- [ ] 10.3 `[YOU]` Upload to Google Sheets and set sharing (see Q4). **Done when:** the link works.

## 11. Map (GIS)

- [x] 11.1 Folium map: tier layers, clusters, popups, choropleth, attribution. (R10.1, R10.2, R10.5, R10.6) **Done when:** served over HTTP, tiles load and layers toggle.
- [x] 11.2 Static PNG. (R10.3, R10.5) **Done when:** the image is legible at slide size.
- [x] 11.3 District summary CSV. (R10.4) **Done when:** district counts add up to the located lead total.

## 12. Documentation and release

- [x] 12.1 README, including setup, €0 explanation, paid-LLM scaling, limits, GDPR and UWG. (R11.7) **Done when:** the setup steps work on a clean environment.
- [x] 12.2 `outputs/approach.md` with Mermaid diagram and "what I would build next". **Done when:** every prompt item is covered.
- [x] 12.3 `outputs/run_summary.md`. (R11.4) **Done when:** counts match the ledger and the cost is shown.
- [x] 12.4 Full test suite and end-to-end rerun from cache. (R11.6, G6) **Done when:** tests pass and the rerun sends no paid request.
- [ ] 12.5 `[YOU]` Create the public GitHub repository and approve the push; I check no personal data or secrets are included first. **Done when:** repository is live.
- [ ] 12.6 Host the interactive map at a public URL. (R12.3) **Done when:** the URL loads with tiles while logged out.
- [x] 12.7 Build the 6-8 slide presentation, including the map image and validation results. (R12.1, R12.2) **Done when:** it has a shareable link.
- [ ] 12.8 `[YOU]` Open every link logged out, using the reply I draft, and send it to Heidi by Tuesday 18:00. (R12.4) **Done when:** sent.
