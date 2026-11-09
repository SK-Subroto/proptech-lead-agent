# Run summary

Date: 2026-11-09  
City: Berlin  
Cost: EUR 0

## Stages

| Stage | Seconds | Counts |
|---|---:|---|
| collect | 554.4 | {"osm_rows": 94, "google_rows": 1345, "after_dedup": 1356, "candidates": 1000, "with_website": 962, "excluded_listed": 6, "google_only_not_listed": 350} |
| crawl | 1048.7 | {"companies": 1000, "status_ok": 868, "status_robots_blocked": 65, "status_no_website": 38, "status_error": 28, "status_timeout": 1, "pages_fetched": 3480, "requests_this_month": 4562} |
| rules | 29.5 | {"extracted": 866, "no_management_evidence": 248} |
| geo | 9.2 | {"located": 792, "unlocated": 208, "outside_boundary": 61, "with_district": 731, "nominatim_requests_this_month": 698} |
| contacts | 0.4 | {"companies": 1000, "named_contact": 569, "email_generic_only": 531, "email_none": 403, "email_found_on_site": 43, "email_pattern_inferred": 23} |
| score | 4.6 | {"leads": 535, "excluded_after_crawl_or_geo": 2, "google_only_not_listed": 463, "tier_B": 509, "tier_A": 26, "mean_completeness": 21.5} |
| validate | 0.2 | {"leads": 535, "sample": "kept existing filled-in sample", "possible_duplicates": 8, "flag_name_needs_review": 52, "flag_self_managed_landlord": 49, "flag_management_unconfirmed": 27, "flag_email_unverified_pattern": 16, "flag_possible_duplicate": 8, "flag_website_robots_blocked": 5, "flag_postcode_outside_city": 4, "flag_website_error": 1} |
| export | 0.9 | {"leads_rows": 535, "top_rows": 25, "excluded_rows": 8} |
| map | 3.0 | {"districts": 12, "leads_with_district": 515, "leads_on_map": 515} |

Total recorded stage time: 1650.9 seconds.

## External requests

| Month | Service | Requests |
|---|---|---:|
| 2026-11 | overpass | 1 |
| 2026-11 | crawl | 4562 |
| 2026-11 | geoportal_wfs | 1 |
| 2026-11 | google_text_search_enterprise | 297 |
| 2026-11 | nominatim | 698 |

Stage 3b ran in Claude Code on the owner's Pro plan: no API charge.
