# DDOT Occupancy Permit Enrichment (FHWA Functional Class)

Enriches 2026 Public Space Occupancy Permit records for **DC Water, PEPCO and
Washington Gas** with the FHWA functional class of each permit location, plus
the highest functional class per permit application.

## What it produces

`output/enriched_occupancy_permits_2026.csv`: **one row per permit application
location**. Key columns:

| Column | Meaning |
|---|---|
| `TrackingNumber` | Permit *application* ID (used to group locations) |
| `PermitNumber` | Permit number incl. revision suffix (blank until issued) |
| `utility`, `utility_matched_on` | Which utility, and which field identified it |
| `LocationID`, `WorkLocationFullAddress` | The individual work location |
| `location_functional_class(_code)` | Functional class of the roadway at this location |
| `lrs_route_name`, `lrs_unit_block`, `lrs_offset_m` | The roadway the point was matched to |
| `street_name_match` | QA: does the matched roadway's street name appear in the permit address? |
| `lookup_status` | `ok`, `no_match`, `alley_or_no_class`, `error`, `no_coordinates` |
| `permit_location_count` / `permit_locations_resolved` | Locations on the application / how many got a class |
| `permit_highest_functional_class(_code)` | Highest class across all locations of the application |
| `permit_highest_fc_location` | The location that produced that highest class |
| `permit_highest_fc_complete` | `False` if any location is unresolved (highest class may be understated) |

Other outputs: `output/enrichment.log`, `output/functional_class_codes_observed.csv`
(every code/label pair seen), `cache/` (raw permit pull + roadway lookup cache).

## How to run (Windows 11)

1. Install Python 3.9 or newer from python.org (tick "Add python.exe to PATH").
2. Unzip the folder, open **PowerShell** in it, then:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python enrich_permits.py
```

If PowerShell blocks the activate script, run
`Set-ExecutionPolicy -Scope Process Bypass` first.

A full run makes about 5,000 roadway lookups. The LRS service is slow (about 4 s
per request), so expect roughly 1.5-2 hours with the default 4 workers (the
reference run took 1 h 38 min). It is resumable, so an interrupted run loses nothing.
**Quick test:** `python enrich_permits.py --limit 25` writes a `_SAMPLE.csv`.

### Options

| Option | Default | Notes |
|---|---|---|
| `--start-date YYYY-MM-DD` | `2026-01-01` | Application **or** issue date on/after this |
| `--workers N` | `4` | Parallel lookups. Use `1` if the server errors |
| `--search-radius M` | `100` | Passed to the LRS (the service fails without it) |
| `--refresh` | off | Ignore the cache and redo every lookup |
| `--output-dir PATH` | `output/` | |

Other settings (permit types, utility patterns, URLs, timeouts) are constants
at the top of `enrich_permits.py`.

**The script is resumable.** Each lookup is saved to `cache/lrs_lookup_cache.csv`
as it completes. If a run is interrupted, re-run the same command; finished
locations are skipped and `error` results are retried.

## Method

1. **Permits**: queried from the TOPS layer (`MapServer/24`) with paging
   (limit 2,000/request). Filter: `ApplicationDate >= start OR IssueDate >= start`.
2. **Permit types**: exact match on `EventTypeDescription` to
   *Construction Staging Area* and *Mobile Crane Work Zone*.
3. **Utilities**: no utility field exists, so `ApplicantCompany`, `OwnerName`
   and `PermitteeName` are searched with case-insensitive patterns covering the
   spellings seen in the data (e.g. `Wash Gas & Light Co.`, `DCWater/OPS`,
   `Potomac Electric Power Company`, `Pepco Inc`). In the data reviewed, every
   match came from `ApplicantCompany`.
4. **Locations**: the layer is not one-point-per-permit; each row is one work
   location and a permit repeats once per `LocationID`. Rows are kept per location.
5. **Roadway lookup**: `getPointOnSubBlock` is called with the point
   `OnSegX/OnSegY` (Maryland State Plane, meters, WKID 26985; already snapped to
   the roadway). If those are missing, lat/lon is reprojected as a fallback.
   All route types within the radius are returned, then **alleys and results with
   no functional class are discarded**. The **nearest** remaining candidate is
   chosen; a street-name match with the permit address only breaks ties within
   2 m (e.g. at an intersection) and is otherwise reported in `street_name_match`.
6. **Highest class**: DC's codes rise as roads get more minor (e.g. 19 = Local),
   so "highest" = the **lowest** code across the application's locations.
   Grouping is by `TrackingNumber` (the application), since `PermitNumber` is blank
   for unissued applications and carries revision suffixes.

## Results (reference run, 2026-10-13)

- 19,651 raw records -> 7,194 after the permit-type filter -> **4,926 location rows**
  after the utility filter (DC Water 1,887; Washington Gas 1,666; PEPCO 1,373),
  covering **3,102 permit applications**. Every utility match came from
  `ApplicantCompany`.
- **All rows are Construction Staging Area.** None of the 256 Mobile Crane Work
  Zone permits has DC Water, PEPCO or Washington Gas in the applicant, owner or
  permittee fields, so that type contributes no rows.
- Lookups: 4,924 resolved, 2 unresolved (3400 BENNING ROAD NE and 1900
  MASSACHUSETTS AVENUE SE: no classified, non-alley roadway within 100 m). Both are
  single-location applications, so their highest class is blank.
- Locations by functional class (code: count): 11 Interstate: 7; 12 Other Freeway
  and Expressway: 1; 14 Principal Arterial: 498; 16 Minor Arterial: 1,089;
  17 Major Collector: 808; 18 Minor Collector: 107; 19 Local: 2,414.
- Highest class per application: Local 1,530; Minor Arterial 663; Major Collector
  521; Principal Arterial 314; Minor Collector 64; Interstate 7; Other Freeway 1;
  unresolved 2. 342 applications have locations in more than one class.
- Match quality: median distance from the roadway is 0 m; 120 rows are >10 m away
  (13 are >50 m). **92 rows have a street-name mismatch** (`street_name_match =
  False`). 55 of these are ~0 m from a road and are mostly point addresses on corner
  lots whose coordinate sits on the cross street, so the class may belong to the
  cross street. 37 are >10 m away and merit manual review. In all 92 applications
  the mismatched location is the one that sets the highest class.

## Assumptions and judgment calls

- "Utility" means the three named companies only. Contractors working on their
  behalf (e.g. Fort Myer, Capitol Paving) and telecoms are excluded.
- Individuals listed as `OwnerName` (e.g. staff or agents) are excluded unless a
  checked field matches a utility.
- Date fields are epoch milliseconds; they are formatted as `YYYY-MM-DD` without
  timezone shifting (`DATE_TZ = "UTC"`) because the service appears to store local
  time as UTC.
- Permits with an application date before 2026 but issued in 2026 would be included,
  per "applications **or** issued permits on/after January 1, 2026" (no such
  record occurred in the data, so every row has a 2026 application date).
- All statuses are kept (including Rejected, Withdrawn, Revoked: 90 rows; and
  unissued applications with no `PermitNumber`: 2,495 rows), because the brief
  asks for permit *applications* as well as issued permits. Filter on
  `StatusDescription` downstream if only issued permits are wanted.

## Known issues

- The LRS operation returns a generic HTTP-200 `error 500` unless **`outSR` and
  `searchRadius`** are supplied. The script always sends both.
- `routeType` accepts only `Street`, `Service`, `Ramp`, `Alley`, `Driveway` or
  `Trail` (case-insensitive, full word). **`All` is rejected**, even though it is
  the service default, so the parameter is omitted by default. Alleys come back
  with no block name and no functional class, which is why they are filtered out.
  Set `ROUTE_TYPE = "Street"` in the script to filter alleys server-side instead.
- No permit address names an alley (a text search for "ALLEY" only finds
  HALLEY/VALLEY), so alleys only matter as a possible nearest-roadway snap.
- ArcGIS reports failures inside a 200 response; the script checks for the
  `error` key and retries with backoff.
- The service can be slow or temporarily unavailable. Persistent failures are
  recorded as `lookup_status = error`; re-run later to retry only those.
- `street_name_match = False` rows are not removed, only flagged; the nearest
  point can occasionally snap to an intersecting street. Review these.
- Permits with unresolved locations have `permit_highest_fc_complete = False`.

## With more time

- Retry the 2 unresolved locations with a larger search radius and/or
  `ROUTE_TYPE = "Street"`, and review the 37 mismatched rows with offsets >10 m.
- For corner-lot point addresses, prefer the roadway named in the address when it
  is within a larger tolerance (currently 2 m) and compare how many results change.

- Compare `ROUTE_TYPE = "Street"` (server-side) against the default (all types,
  client-side alley filter) to see whether ramps/service roads change any results.
- Cross-check a sample against the Roadway SubBlock layer (option 2 in the brief).
- Add automated tests and a manual-review list of utility name variants not matched.
