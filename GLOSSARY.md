# GrowthOS Analyst — Glossary

## How to use this glossary

This file is **normative for names and meanings only**: it is the single place to resolve "what do we call this, and what does it mean" for the analyst package. It is **not** authoritative for formulas, thresholds, arithmetic, or implementation — those are enforced by the reviewed CLI and described in `growthos/metric_registry.json` and the shipped skills.

1. Every term has exactly one canonical definition in this file.
2. Cross-references use links rather than duplicated definitions.
3. A validation check rejects duplicate term identifiers and broken links.

Metric entries name the concept and point at their registry ID rather than repeating the machine contract. Where the registry mirrors a threshold for runtime explanation, the reviewed CLI is authoritative. Add each fact once, in its owning artifact, then link to it.

This glossary covers metrics, funnel, targets, investigation, output statuses, and provenance.

## Product & Workflow

**GrowthOS**:
An AI-native business operating system that pairs a conversational agent with deterministic analytical tools and SME skills.
_Avoid_: Dashboard, chatbot

**Skill**:
A reviewed, reusable SME business procedure specifying when and how to investigate a growth question, invoke tools, interpret evidence, and handle missing data.
_Avoid_: Prompt, script

**Assessment Layer**:
The first operating layer: reports IRF numbers, compares periods, evaluates them against targets, and filters cities into a Target-Miss Shortlist. It establishes *where* performance is off target and does not investigate causes.
_Avoid_: Layer 1

**Investigation Layer**:
The second operating layer: takes shortlisted or explicitly named scopes through the three steps that explain an FTU target miss or drop.
_Avoid_: Layer 2

**Step 1 — Registration Target Shortfall Check**:
Tests whether Registrations fell short of their own target by enough to explain the FTU shortfall. A large enough shortfall stops the investigation at Registrations.

**Step 2 — Month-on-Month Dip and Persistence Check**:
Tests whether FTU genuinely dropped versus the prior month once calendar length is accounted for, and whether the city was already a material miss in the prior month. Either a non-dip or a repeat miss stops the investigation before funnel analysis.

**Step 3 — Funnel Decomposition**:
Breaks an FTU drop into what moved at Fare Estimates, request conversion, and completion, then selects the largest contributors.

## Reporting & Provenance

**Ended Month**:
A completed calendar month in `Asia/Kolkata`; the only reportable period. No partial-month, week-to-date, or in-month pacing figures.
_Avoid_: Current month, MTD

**Month-on-Month (MoM)**:
A comparison of two adjacent months.
_Avoid_: Sequential comparison, month-over-month growth

**Period Comparison**:
A comparison of two non-adjacent months. Monthly distinct counts are never summed into a quarterly unique total.
_Avoid_: Quarterly total, QoQ

**Provenance**:
Where a month's numbers came from: the source reads performed, their read timestamps, and the stored checksums.

**Verification**:
The structural and checksum validation of stored data; a valid result says the stored facts are internally consistent, not that they are complete or business-approved.

**Nonproduction Data**:
Synthetic or test data that must be labelled as such and never quoted as a business fact.

## Acquisition & IRF Funnel

**IRF (Install, Registration, First-Time User)**:
Rapido's new-user acquisition funnel tracking monthly app installations, first-time customer registrations, and first-ever completed rides across cities.

**Installs**:
New Rapido app installations in a reporting month; counts installations rather than unique lifetime people because existing customers can uninstall and reinstall.
_Avoid_: Unique installers, new users
_Registry_: `installs`

**Registrations**:
Customers completing their first registration with Rapido in a reporting month (approved `entity_reg` concept; reinstalling or logging back in does not create a new registration).
_Avoid_: AppsFlyer registrations (`af_reg`), logins
_Registry_: `registrations`

**Platform FTU (PFTU)**:
Customers completing their first-ever dropped (completed) Rapido ride in a reporting month, counted once per customer across all services.
_Avoid_: SFTU, Net Rides
_Registry_: `platform_ftu`

**Bike/Link PFTU**:
Customers whose first-ever completed Rapido ride in a reporting month was on the Bike/Link (`link`) service, counted once per customer.
_Avoid_: SFTU
_Registry_: `platform_link_ftu`

**Auto PFTU**:
Customers whose first-ever completed Rapido ride in a reporting month was on Auto.
_Avoid_: SFTU
_Registry_: `platform_auto_ftu`

**Cab PFTU**:
Customers whose first-ever completed Rapido ride in a reporting month was on Cab; excludes c2c.
_Avoid_: SFTU
_Registry_: `platform_cab_ftu`

**SFTU (Service FTU)**:
An existing Rapido customer taking a specific service for the first time after completing an earlier ride on another Rapido service. Distinct from Platform FTU and the service PFTU family; not tracked in GrowthOS V1.
_Avoid_: SVN

**I2R (Install-to-Registration Ratio)**:
Registrations divided by Installs for the same reporting period; compares period totals rather than a matched install cohort.
_Registry_: `i2r`

**R2N (Registration-to-PFTU Ratio)**:
Platform FTU divided by Registrations for the same reporting period; compares period totals rather than a matched registration cohort.
_Avoid_: `RR2Net`
_Registry_: `r2n`

**Bike/Link PFTU Share**:
Bike/Link PFTU divided by total Platform FTU in the same period. Shares need not sum to 100% because unselected services still count in Platform FTU.
_Avoid_: Service mix, denominator share
_Registry_: `link_share`

**Auto PFTU Share**:
Auto PFTU divided by total Platform FTU in the same period.
_Avoid_: Service mix, denominator share
_Registry_: `auto_share`

**Cab PFTU Share**:
Cab PFTU divided by total Platform FTU in the same period.
_Avoid_: Service mix, denominator share
_Registry_: `cab_share`

**Bike-vs-Auto Share Gap**:
Bike/Link PFTU share minus Auto PFTU share; movement is expressed in `pp`.
_Registry_: `link_vs_auto_share_gap`

## Ride Funnel & Fulfillment

**FTU-eligible Cohort**:
The population behind fare estimates, requests, net rides and their ratios: customers who had completed their first ride by month-end, tracked on their first-ride day, plus Zero-Transaction customers with no completed ride yet.
_Avoid_: All customers, active users

**Zero-Transaction Customer**:
A customer in the FTU-eligible cohort with no completed ride in the period; retained with zero funnel activity rather than dropped.

**Fare Estimate (FE)**:
An FTU-eligible session in which pickup and drop locations are entered and prices across service categories are displayed, summed as the daily customer-maximum sessions over the month.
_Avoid_: Distinct FE users
_Registry_: `fe_sessions`

**Rapido Requests (RR)**:
An FTU-eligible session in which a customer selects a service and requests a ride, counted across ride-funnel services.
_Registry_: `rr`

**Net Rides (Net)**:
Completed (dropped) orders by the FTU-eligible cohort across ride-funnel services in a reporting month; includes same-day subsequent rides on the first-ride day.
_Avoid_: Platform FTU
_Registry_: `net`

**FE2RR**:
Demand conversion ratio: the share of Fare Estimate sessions that convert into Rapido Requests.
_Registry_: `fe2rr`

**RR2Net**:
Fulfillment ratio: the share of Rapido Requests that convert into completed Net Rides.
_Avoid_: `R2N`
_Registry_: `rr2net`

## Service Ride Funnel

**Bike RR**:
Rapido Request sessions for the approved Bike service family within the FTU-eligible cohort.
_Registry_: `bike_rr`

**Bike Net**:
Completed (dropped) Bike orders within the FTU-eligible cohort.
_Registry_: `bike_net`

**Auto RR**:
Rapido Request sessions for the approved Auto service family within the FTU-eligible cohort.
_Registry_: `auto_rr`

**Auto Net**:
Completed (dropped) Auto orders within the FTU-eligible cohort.
_Registry_: `auto_net`

**Cab RR**:
Rapido Request sessions for the approved Cab service family within the FTU-eligible cohort; excludes c2c.
_Registry_: `cab_rr`

**Cab Net**:
Completed (dropped) Cab orders within the FTU-eligible cohort; excludes c2c.
_Registry_: `cab_net`

**Bike RR2Net**:
The share of Bike Rapido Requests that convert into completed Bike Net rides.
_Registry_: `bike_rr2net`

**Auto RR2Net**:
The share of Auto Rapido Requests that convert into completed Auto Net rides.
_Registry_: `auto_rr2net`

**Cab RR2Net**:
The share of Cab Rapido Requests that convert into completed Cab Net rides.
_Registry_: `cab_rr2net`

## Geography & Tiers

**Canonical City**:
The standardized city entity used across reporting after approved city merges (for example Attingal merged into Trivandrum, Guntur into Vijayawada, and Faridabad into Delhi).

**City Tier (T1, T2, T3)**:
Market classification grouping cities into three tiers; cities absent from the canonical classification map default to T3, with unmapped-city counts disclosed.
_Avoid_: T4, Unclassified

**Paired-Month Tier Cohort**:
For period comparisons, each city is counted in its latest observed tier within the compared month pair, so tier totals still sum to the overall total if a city changes tier across months.

**Selected-Month Tier Cohort**:
For targets and investigation, only the tier stored for the selected month is used, and only plan-matched cities are included. The prior month's stored tier is not required to match.

## Targets & Target Misses

**Platform FTU Target**:
The planned number of Platform FTUs for a month from the city-level target plan; a planning input, not an observed actual.
_Registry_: `platform_ftu_target`

**Monthly Target Plan**:
The supplied monthly target set for Platform FTU or Registrations: city targets, the Grand Total, and the single New Launch target.

**Grand Total Target**:
The authoritative national monthly target for Platform FTU or Registrations, independent of the sum of individual city targets.

**Plan-Matched City**:
A city that has its own stored target for the selected month.

**Unplanned City**:
An observed city with no stored target for the selected month. It has no individual target or variance, never enters the plan-matched ranking, and is evaluated only through the New Launch fallback pool.

**New Launch Fallback Pool**:
All observed unplanned cities, evaluated together as one national cohort against the single monthly New Launch target.
_Avoid_: Per-city fallback target

**Target Variance**:
The signed difference `actual - target`, where negative means a miss and positive means a beat.
_Avoid_: Signed shortfall
_Registry_: `target_variance`

**Variance Percentage**:
Target Variance expressed as a percentage of target, so it can be compared across differently sized cities.
_Registry_: `variance_percentage`

**Coverage Shortfall (X)**:
The national shortfall the shortlist must cover: the amount by which the overall target exceeds observed actual, floored at zero. `top` mode keeps selecting cities until their cumulative shortfall covers it.
_Avoid_: Gap to target, total miss

**Target-Miss Shortlist**:
The ranked list of Material Misses in shortfall order: the Assessment Layer's output and the Investigation Layer's default starting point.
_Registry_: `target_shortlist`

**Shortlist Mode (top / all)**:
`top` selects ranked cities until cumulative shortfall covers X; `all` returns every qualifying city even when X is zero.

**Target Queue (matched / fallback)**:
Which target cohort is being analysed: `matched` for cities with their own targets, `fallback` for the pooled New Launch cohort.
_Avoid_: Batch, list

**Material Miss**:
Observed FTU short of a positive target by more than the configured margin. It applies to the default shortlist queue (plan-matched cities), the New Launch pool as one cohort, and the widening annotation (plan-matched page rows only).
_See_: `targets.target_shortlist` in `growthos/metric_registry.json` for the shipped qualification rules.

**Widening Miss (`persistent` / `new_miss`)**:
A current material miss whose variance percentage worsened by more than the configured margin versus the immediately prior month. `persistent` means it was also a material miss in the prior month; `new_miss` means it was not. Missing or nonpositive prior operands leave the status undetermined, not failed.
_See_: `targets.target_persistence` in `growthos/metric_registry.json` for the shipped prior-month and sign rules.
_Registry_: `target_persistence`

## Metric Movements

**Absolute Change**:
The signed difference between a metric's latest and previous period counts (`latest - previous`).
_Registry_: `absolute_delta`

**Volume Growth**:
The relative change in a count metric over its previous period; unavailable when either operand is missing or the previous count is zero.
_Registry_: `growth_fraction`

**Ratio Movement**:
The signed percentage-point change between two ratios; a move from 40% to 45% is `+5pp`, not `+5%`.
_Registry_: `delta_pp`

## Investigation & Diagnosis

**Contribution**:
The share of an FTU gap or drop explained by one upstream factor, such as Registrations or a single Funnel Stage. Contributions can exceed 100% because factors overlap.

**Counterfactual (hold-two-constant)**:
Re-running the comparison with one Funnel Stage held at its prior value, to estimate how much that stage alone moved FTU. It is an accounting signal, not proof of cause.

**Funnel Stage**:
One of `FE` (fare-estimate volume), `FE2RR` (demand conversion), or `RR2Net` (fulfillment). Not to be confused with the three investigation Steps.

**Calendar Adjustment**:
Scaling the longer month's volumes down to the shorter month's day count so a month-on-month comparison is like-for-like. Ratios are unchanged by the scaling.

**Scope**:
The unit under analysis: a city, a City Group, a tier, the nation, or the New Launch cohort.

**City Group**:
An analyst-defined set of cities analysed together, pooled before ratios are calculated.
_Avoid_: Cluster, segment

**Primary Issue**:
A Funnel Stage selected as a main explanation after the stage floor and cumulative-coverage filters.

**Over-Explained**:
A flag meaning overlapping contributions sum above 100%; it signals double counting across factors, not an arithmetic error.

**Coverage Disclosure**:
The included and excluded city counts reported alongside an aggregate, so partial coverage is never mistaken for a complete total.

**Failing Since**:
The month a city's current miss streak began; cited for a `persistent` miss.

## Output Vocabulary

User-visible verdicts and statuses the agent may quote back. Exact arithmetic and selection rules are enforced by the reviewed CLI and mirrored in `growthos/metric_registry.json`; internal field names do not belong here. Unavailable results are disclosed with a reason code rather than a verdict.

**`above` · `at` · `below` · `unavailable`**:
Target attainment states for a city: over target, exactly on target, under target, or missing an operand.

**`registration_issue`**:
Registrations explain more than the configured stop share of the FTU target gap; the investigation stops at Registrations.

**`no_registration_shortfall`**:
Registrations met or beat target, so the FTU gap is not explained by registration volume; the investigation continues.

**`below_threshold`**:
A contribution above zero but at or below the configured stop threshold; the investigation continues.

**`not_applicable`**:
The check does not apply — for Step 1 when FTU met its target, for Step 2 when there was no material dip.

**`no_historical_ftu_dip`**:
FTU did not fall month over month after calendar adjustment; funnel decomposition does not apply.

**`no_material_ftu_dip`**:
FTU fell, but within the configured material-dip margin; funnel decomposition stops while factual rates are still reported.

**`persistent`**:
The current material miss was also a material miss in the prior month; the answer cites the prior month instead of decomposing the current one.

**`new_miss`**:
A current material miss that was not a material miss in the prior month; funnel decomposition proceeds.

**`contributors_identified`**:
Funnel decomposition found at least one primary issue that covered the configured cumulative share of the drop.

**`partial_contributors_identified`**:
Funnel decomposition found material contributors, but together they did not reach the configured cumulative share; the answer must say the explanation is partial.

**`partial_no_material_contributor`**:
There is a material dip, but no stage clears the stage floor; only a partial explanation is available.

**`no_material_funnel_contributor`**:
There is a material dip, but no stage contributes materially at all.

**`material_contributor`**:
A Funnel Stage contributing more than the configured stage floor to the FTU change; eligible for primary-issue selection.

**`no_stage_deterioration`**:
A Funnel Stage did not worsen month over month, so it is not a contributor.

**`undetermined`**:
A required month, target, observation, or positive operand is unavailable; the reason is disclosed and nothing is inferred.

**`over_explained`**:
A flag on a result whose contributions sum above 100%.