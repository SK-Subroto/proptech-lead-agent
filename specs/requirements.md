# Requirements: Berlin Hausverwaltung Lead Engine

Status: **Draft, awaiting approval** (Phase 0, drafted 2026-11-08)
Case study deadline: Tuesday 2026-11-10 18:00 Berlin. Internal finish: Tuesday 12:00.

Requirements use EARS style. `[CHANGED]` marks a deliberate departure from `CLAUDE_CODE_PROMPT.md`; the reason is in `design.md` section 12.

## Goals

| ID | Goal | Measure |
|---|---|---|
| G1 | Coverage | As many real Berlin Hausverwaltungen as possible after deduplication; 400 is our minimum, not a stopping point |
| G2 | Data quality | At least 90 % field accuracy on a random manual sample of 30 rows |
| G3 | Traceability | Every enriched value has a source URL and a confidence label |
| G4 | Usefulness | Every company has a score (0-100), a tier (A/B/C) and a one-line reason |
| G5 | Contacts | Named decision-maker for at least 60 % of companies; email status always labelled |
| G6 | Reusability | One command reruns the pipeline; city is a config value |
| G7 | Cost | EUR 0, proven by logged request counts per stage |
| G8 | GIS | Every located lead is assigned to an official Berlin district and shown on an interactive map and a static PNG |

## User stories and acceptance criteria

### R1. Collect companies

*As the applicant, I want a broad, source-labelled company list so that coverage is defensible.*

- R1.1 WHEN the collect stage runs THE SYSTEM SHALL query Overpass for the configured city using `office=property_management` and name matches for Hausverwaltung, Immobilienverwaltung, Mietverwaltung and upper-case `WEG`.
- R1.2 WHEN an Overpass response is cached THE SYSTEM SHALL reuse it instead of sending a new query, and SHALL send at most one query at a time.
- R1.3 IF `GOOGLE_MAPS_API_KEY` is absent or `google.mode` is `off` THEN THE SYSTEM SHALL skip the Google source, log the skip, and continue.
- R1.4 WHILE the Google source runs THE SYSTEM SHALL keep the adaptive grid tiling and deduplication logic from `collect_hausverwaltungen.py`.
- R1.5 WHEN a Google request would take the month's recorded total above `google.monthly_request_budget` (default 900) THE SYSTEM SHALL stop calling Google and continue with what it has. `[CHANGED]`
- R1.6 WHEN `google.mode` is `discovery_only` THE SYSTEM SHALL publish no Google-origin field other than `google_place_id`, and SHALL NOT use Google coordinates for mapping or spatial analysis. `[CHANGED]`
- R1.7 THE SYSTEM SHALL keep the VDIV Berlin-Brandenburg source disabled by default, and SHALL only enable it when `vdiv.permission_confirmed` is true. `[CHANGED]`
- R1.8 THE SYSTEM SHALL record every contributing source in a `sources` column.
- R1.9 WHEN merging THE SYSTEM SHALL deduplicate by normalised domain, then normalised name plus postcode, then fuzzy name match.
- R1.10 WHEN a website is on a shared platform (for example facebook.com) THE SYSTEM SHALL NOT use its domain as a deduplication key. `[CHANGED]`
- R1.11 WHEN a row matches a non-target rule (estate agent only, Facility Management only, Hausmeisterservice and similar) THE SYSTEM SHALL flag it with a reason and move it to `Excluded`, never delete it silently.

### R2. Crawl websites

*As the applicant, I want clean page text per company so that extraction is cheap and repeatable.*

- R2.1 WHEN a company has a website THE SYSTEM SHALL fetch the home page, the Impressum and up to 3 relevant pages (Leistungen, Über uns, Team, Kontakt, Karriere).
- R2.2 IF robots.txt disallows a URL THEN THE SYSTEM SHALL skip it and record `robots_blocked`.
- R2.3 WHILE crawling THE SYSTEM SHALL send a descriptive User-Agent, rate-limit per domain, use timeouts and retry transient failures.
- R2.4 WHEN a page is already cached on disk THE SYSTEM SHALL use the cache and send no request.
- R2.5 WHEN a company is crawled THE SYSTEM SHALL write `data/interim/pages/<company_id>.md` with the URL above each section, trimmed to the most relevant ~6,000 characters.

### R3. Extract with rules

- R3.1 WHEN pages exist THE SYSTEM SHALL extract company name, address, postcode, manager names, emails, phones, Handelsregister number and legal form with code only.
- R3.2 WHEN pages exist THE SYSTEM SHALL detect metering providers, software, Eigentümerportal, Funk, fernablesbar, HeizkostenV and job ad keywords.
- R3.3 THE SYSTEM SHALL save each value to `data/interim/rules/<company_id>.json` with `source_url` and matched text.
- R3.4 IF a keyword is not found THEN THE SYSTEM SHALL record `not_mentioned`, not `false`, because absence from up to 5 pages is not evidence of absence.

### R4. Extract with Claude Code

- R4.1 WHEN stage 3b runs Claude Code SHALL fill only units managed, management types, employees, founding year, other cities, digital maturity and company summary, in batches of about 20.
- R4.2 THE SYSTEM SHALL validate every record against a Pydantic schema in which each field is `{value, source_url, evidence_quote, confidence}`.
- R4.3 IF a value is not stated in the supplied text THEN THE record SHALL contain `null`.
- R4.4 THE SYSTEM SHALL process companies in priority order and skip those that already have a file.
- R4.5 WHEN a batch ends THE SYSTEM SHALL run the validator and update `data/interim/llm/_progress.md`.
- R4.6 THE SYSTEM SHALL expose `backend = claude_code | ollama | anthropic_api`, with only `claude_code` implemented.

### R5. Geo enrichment (GIS)

*As the applicant, I want each lead placed in an official district so that the territory view is correct.* `[CHANGED: added stage]`

- R5.1 WHEN the geo stage first runs THE SYSTEM SHALL download district polygons from the source in the city config and cache them under `data/raw/geo/`.
- R5.2 WHEN a company has OSM coordinates THE SYSTEM SHALL use them, with `geo_source = osm`.
- R5.3 WHEN a company has no OSM coordinates but has an Impressum address THE SYSTEM SHALL geocode it through Nominatim in a single thread at no more than 1 request per second, and cache the result.
- R5.4 WHEN a company has coordinates THE SYSTEM SHALL assign `district` by point-in-polygon and set `in_city_boundary`.
- R5.5 IF the point lies outside the city boundary THEN THE SYSTEM SHALL move the row to `Excluded` with reason `outside_city_boundary`.
- R5.6 IF a company cannot be located THEN THE SYSTEM SHALL leave coordinates `null`, keep the row, and report the unlocated count.

### R6. Contacts

*Contacts run before scoring because reachability is a scored KPI.* `[CHANGED: order]`

- R6.1 THE SYSTEM SHALL use the Impressum and Team pages as primary contact sources.
- R6.2 THE SYSTEM SHALL prefer Geschäftsführer/Inhaber, then Leitung WEG-/Mietverwaltung, then Leitung Heizkostenabrechnung/Betriebskosten.
- R6.3 THE SYSTEM SHALL label every company `found_on_site`, `pattern_inferred`, `generic_only` or `none`.
- R6.4 THE SYSTEM SHALL write a `linkedin_search_url` and an empty `linkedin_profile_url`, and SHALL NOT scrape LinkedIn.
- R6.5 THE SYSTEM SHALL NOT probe mail servers or send any email.
- R6.6 THE SYSTEM SHALL write a `xing_search_url` for every named contact.
- R6.7 THE SYSTEM SHALL report the share of companies with a personal email separately from the share with a named contact.

### R7. Score

- R7.1 THE SYSTEM SHALL read all weights and rules from `config/scoring.yaml`.
- R7.2 THE SYSTEM SHALL output `lead_score` (0-100), `lead_tier` (A at 70 or above, B 40-69, C below 40) and `score_reason`.
- R7.3 IF a KPI is missing THEN THE SYSTEM SHALL apply the configured neutral value and reduce `data_completeness`.
- R7.4 THE SYSTEM SHALL build `score_reason` from matched rule labels only.

### R8. Validate

- R8.1 THE SYSTEM SHALL check duplicates, postcode range 10115-14199, URL and email format, website reachability and plausible numeric ranges, and write the results to `qa_flags`.
- R8.2 THE SYSTEM SHALL write a 30-row `validation_sample.xlsx` using a fixed seed, with empty correct / wrong / not verifiable columns per field.
- R8.3 WHEN the sample is filled in THE SYSTEM SHALL compute per-field accuracy and write `validation_report.md`.

### R9. Export

- R9.1 THE SYSTEM SHALL write `outputs/hausverwaltungen_berlin.xlsx` with tabs `Leads`, `Top 25`, `KPI Definitions`, `Method & Sources`, `Validation` and `Excluded`, and one CSV per tab.
- R9.2 `Leads` SHALL be sorted by score, with frozen header, filters and tier colours; the file SHALL contain no macros and only simple conditional formatting.
- R9.3 `Top 25` SHALL list the highest-scoring Tier A leads with reason and first-contact angle. IF fewer than 25 are Tier A THEN it SHALL list only those.
- R9.4 `Method & Sources` SHALL state sources, licences, tools, run date, counts per stage, request counts and cost.
- R9.5 IF a KPI value is unknown THEN the `Leads` tab SHALL show `not stated` rather than a blank cell, and SHALL NOT substitute an estimate.
- R9.6 `Method & Sources` SHALL show the fill rate per KPI.

### R10. Map (GIS deliverable)

`[CHANGED: promoted from bonus to required]`

- R10.1 THE SYSTEM SHALL write `outputs/lead_map.html`: a Folium map with toggleable Tier A, B and C layers, clustered markers, and popups showing name, score, units and contact status.
- R10.2 THE map SHALL include a district choropleth of lead counts, with a tooltip showing the Tier A/B/C counts and mean score per district.
- R10.3 THE SYSTEM SHALL write `outputs/lead_map.png` using geopandas and matplotlib from district polygons only, without downloading map tiles.
- R10.4 THE SYSTEM SHALL write `outputs/district_summary.csv` with counts per tier, mean score and share of leads per district.
- R10.5 THE map and PNG SHALL show visible OpenStreetMap and Geoportal Berlin attribution.
- R10.6 THE map SHALL NOT display coordinates obtained from Google.

### R11. Non-functional

- R11.1 THE SYSTEM SHALL run on Python 3.11+ using free open-source libraries only.
- R11.2 THE SYSTEM SHALL provide `python -m leadengine run --city Berlin --stages ...` and per-stage commands.
- R11.3 Every stage SHALL read from and write to files so it can be rerun independently.
- R11.4 THE SYSTEM SHALL log counts per stage and write request counts to `data/processed/request_ledger.json`.
- R11.5 THE repository SHALL contain no secrets, and SHALL NOT contain raw crawled pages, Google responses or files with personal contact data. `[CHANGED]`
- R11.6 Tests SHALL cover dedup, postcode filter, spatial join, Impressum and email extraction, email classification, scoring and schema validation, using saved fixtures and no live calls.
- R11.7 The README and `approach.md` SHALL include GDPR and UWG §7 notes stating that first contact should be by phone or another compliant channel.

### R12. Submission to Heidi

- R12.1 THE project SHALL produce a short presentation (6-8 slides) covering sources, tools, automations and validation, with a link anyone can open.
- R12.2 THE presentation SHALL include the static map image.
- R12.3 THE interactive map SHALL be hosted at a public URL.
- R12.4 BEFORE submission every link SHALL be opened in a logged-out browser and confirmed accessible.

## Deliverables

Required by Heidi:

1. Google Sheet (from `outputs/hausverwaltungen_berlin.xlsx`), view-only for anyone with the link.
2. Short presentation with a shareable link.
3. Reply to Heidi by Tuesday 2026-11-10 18:00 Berlin.

Additional:

4. Public GitHub repository with code, specs, README and tests.
5. `outputs/approach.md` (source text for the presentation).
6. `outputs/lead_map.html`, `outputs/lead_map.png`, `outputs/district_summary.csv`.
7. `outputs/run_summary.md`.

The additional items must not delay the required ones.
