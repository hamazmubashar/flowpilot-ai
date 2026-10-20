# Lead scoring rules — `2026.10-v1`

Implemented in `src/flowpilot/domain/scoring.py` as pure lookup tables. Scores are stored with
their `rules_version`; any change to a table or threshold **must** bump the version.

Total = sum of five components, max 100.

| Component | Max | Values |
|---|---|---|
| Budget | 35 | under_10k 5 · 10k_25k 15 · 25k_50k 25 · 50k_100k 30 · over_100k 35 · undisclosed 10 |
| Company size | 20 | 1-10 5 · 11-50 12 · 51-200 20 · 201-1000 18 · 1000+ 15 |
| Timeline | 20 | immediate 20 · 1_3_months 15 · 3_6_months 8 · exploring 3 |
| Service fit | 15 | process_automation 15 · data_analytics 15 · ai_strategy 12 · systems_integration 12 · digital_transformation 10 · other 3 |
| Industry fit | 10 | saas / fintech / logistics / professional_services 10 · healthcare / retail_ecommerce / manufacturing 6 · nonprofit 4 · other 2 |

## Tiers and routing

| Tier | Score | Route |
|---|---|---|
| hot | ≥ 70 | `partner_review` |
| warm | 45–69 | `sales_queue` |
| cold | < 45 | `nurture` |

## Rationale (fictional business context)

Northstar's sweet spot is mid-market (51–1000 staff) operations work with a real budget and a
near-term start. Very large enterprises score slightly lower because of long procurement cycles;
"undisclosed" budget scores above the smallest band because it is common in early enquiries.
