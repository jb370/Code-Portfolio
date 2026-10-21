#!/usr/bin/env python3
"""
DDOT Public Space Occupancy Permit enrichment with FHWA functional class.

Pipeline
--------
1. Pull occupancy permit records from the DDOT TOPS REST feed (layer 24),
   paging through the 2,000-record limit.
2. Keep two permit types (Construction Staging Area, Mobile Crane Work Zone)
   and only permits whose applicant / owner / permittee is DC Water, PEPCO
   or Washington Gas.
3. For every permit *location* (one row per location), query the DDOT Linear
   Referencing System (getPointOnSubBlock) for the nearest roadway, ignore
   alleys, and read the FHWA functional class.
4. Work out the highest functional class per permit application
   (TrackingNumber) and write one CSV row per permit location.

Run:  python enrich_permits.py            (see README.md for options)
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

try:  # only needed for the lat/lon fallback
    from pyproj import Transformer
except ImportError:  # pragma: no cover
    Transformer = None

# =============================================================================
# CONFIGURATION  (edit here or override with command-line options)
# =============================================================================
PERMIT_URL = ("https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_APPS/"
              "DDOT_TOPS/MapServer/24/query")
LRS_URL = ("https://rh.dcgis.dc.gov/dcgis/rest/services/DDOT/RoadwayBlocks/"
           "MapServer/exts/DdotLrsExtensions/getPointOnSubBlock")

START_DATE = "2026-01-01"          # application OR issue date on/after this day
PERMIT_TYPES = ["Construction Staging Area", "Mobile Crane Work Zone"]

# Utilities are matched (case-insensitively) against these fields, in order.
MATCH_FIELDS = ["ApplicantCompany", "OwnerName", "PermitteeName"]
UTILITY_PATTERNS = {
    "DC Water": r"\bD\.?\s?C\.?\s?WATER\b|\bDCWATER\b|\bWASA\b|DISTRICT OF COLUMBIA WATER",
    "PEPCO": r"\bPEPCO\b|POTOMAC ELECTRIC",
    "Washington Gas": r"WASH(?:INGTON)?\.?\s+GAS|\bWGL\b",
}

# Spatial references: OnSegX/OnSegY are Maryland State Plane meters (NAD83).
STATE_PLANE_WKID = 26985
SEARCH_RADIUS = 100                # passed to the LRS (required by the service)
# The LRS rejects routeType="All" (valid: Street, Service, Ramp, Alley, Driveway,
# Trail). Leaving this as None omits the parameter, which the service treats as
# all types; alleys are then dropped client-side. Set to "Street" to filter
# alleys on the server instead.
ROUTE_TYPE = None
TIE_TOLERANCE_M = 2.0              # candidates this close to the nearest are "tied"
WORKERS = 4                        # parallel LRS requests; use 1 if the server struggles
PAGE_SIZE = 2000                   # layer MaxRecordCount
TIMEOUT = 60                       # seconds per request
MAX_RETRIES = 4
BACKOFF_SECONDS = 2                # doubles on every retry
DATE_TZ = "UTC"                    # the service stores local time as UTC; leave as UTC

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / "cache"
OUTPUT_NAME = "enriched_occupancy_permits_2026.csv"
# =============================================================================

PERMIT_FIELDS = [
    "OBJECTID", "TrackingNumber", "PermitNumber", "StatusDescription",
    "EventTypeDescription", "ApplicationDate", "IssueDate", "EffectiveDate",
    "ExpirationDate", "ApplicantCompany", "OwnerName", "PermitteeName",
    "LocationID", "WorkLocationFullAddress", "WorkLocationWard",
    "OnSegX", "OnSegY", "Longitude", "Latitude",
]
DATE_FIELDS = ["ApplicationDate", "IssueDate", "EffectiveDate", "ExpirationDate"]

CACHE_COLS = [
    "lookup_key", "lookup_status", "coord_source", "fc_code", "fc_label",
    "route_name", "route_type", "street_name", "unit_block", "sub_block_key",
    "offset_m", "street_name_match", "n_candidates", "error",
]
RETRY_STATUSES = {"error"}         # re-attempted on the next run

log = logging.getLogger("enrich")
_local = threading.local()
_transformer = None


class ServiceError(Exception):
    """Raised when a REST call fails or returns an ArcGIS error body."""


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def get_session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "ddot-permit-enrichment/1.0"})
        _local.session = session
    return session


def get_json(url: str, params: dict) -> dict:
    """GET with retries. ArcGIS reports many failures as HTTP 200 + an
    {"error": ...} body, so that is checked explicitly."""
    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = get_session().get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "error" in data:
                raise ServiceError(str(data["error"]))
            return data
        except (requests.RequestException, ValueError, ServiceError) as exc:
            last = exc
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
    raise ServiceError(f"{type(last).__name__}: {last}")


# --------------------------------------------------------------------------- #
# Step 1: pull permits
# --------------------------------------------------------------------------- #
def fetch_permits(start_date: str) -> pd.DataFrame:
    where = (f"ApplicationDate >= DATE '{start_date}' "
             f"OR IssueDate >= DATE '{start_date}'")
    rows, offset = [], 0
    while True:
        data = get_json(PERMIT_URL, {
            "where": where,
            "outFields": ",".join(PERMIT_FIELDS),
            "returnGeometry": "false",
            "orderByFields": "OBJECTID",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "f": "json",
        })
        feats = data.get("features", [])
        rows.extend(f["attributes"] for f in feats)
        log.info("Permit pull: %d records so far", len(rows))
        if not feats:
            break
        offset += len(feats)
        if not (data.get("exceededTransferLimit") or len(feats) >= PAGE_SIZE):
            break
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Step 2: filter + classify
# --------------------------------------------------------------------------- #
def classify_utilities(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["utility"] = pd.Series(pd.NA, index=df.index, dtype="object")
    df["utility_matched_on"] = pd.Series(pd.NA, index=df.index, dtype="object")
    for col in MATCH_FIELDS:
        text = df[col].fillna("").astype(str).str.upper()
        for name, pattern in UTILITY_PATTERNS.items():
            hit = df["utility"].isna() & text.str.contains(pattern, regex=True)
            df.loc[hit, "utility"] = name
            df.loc[hit, "utility_matched_on"] = col
    return df


def convert_dates(df: pd.DataFrame) -> pd.DataFrame:
    for col in DATE_FIELDS:
        ms = pd.to_numeric(df[col], errors="coerce")
        stamp = pd.to_datetime(ms, unit="ms", utc=True).dt.tz_convert(DATE_TZ)
        df[col] = stamp.dt.strftime("%Y-%m-%d")
    return df


def prepare_permits(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    log.info("Raw records: %d", len(df))
    for col in ["EventTypeDescription", "ApplicantCompany", "OwnerName",
                "PermitteeName", "WorkLocationFullAddress"]:
        df[col] = df[col].astype("string").str.strip()

    df = df[df["EventTypeDescription"].isin(PERMIT_TYPES)]
    log.info("After permit-type filter: %d", len(df))

    df = classify_utilities(df)
    df = df[df["utility"].notna()].copy()
    log.info("After utility filter: %d  %s", len(df),
             df["utility"].value_counts().to_dict())

    if df["TrackingNumber"].isna().any():
        log.warning("%d rows have no TrackingNumber; they cannot be grouped "
                    "per permit", df["TrackingNumber"].isna().sum())

    # A stable key per location; fall back to OBJECTID if LocationID is blank.
    loc = df["LocationID"].astype("Int64").astype("string")
    df["lookup_key"] = loc.where(df["LocationID"].notna(),
                                 "OID" + df["OBJECTID"].astype("string"))

    before = len(df)
    df = df.drop_duplicates(subset=["TrackingNumber", "lookup_key"])
    if len(df) < before:
        log.info("Dropped %d duplicate (TrackingNumber, location) rows",
                 before - len(df))
    return convert_dates(df)


# --------------------------------------------------------------------------- #
# Step 3: roadway lookup
# --------------------------------------------------------------------------- #
def _num(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def extract_candidates(payload: dict) -> list[dict]:
    out = []
    for p in payload.get("pointOnSubBlocks") or []:
        attrs = (((p.get("roadData") or {}).get("roadwayAttributes") or {})
                 .get("other") or {})
        out.append({
            "fc_code": _num(attrs.get("functionalClassCode")),
            "fc_label": attrs.get("functionalClass"),
            "route_name": p.get("routeName"),
            "route_type": p.get("routeType"),
            "street_name": p.get("streetName"),
            "unit_block": p.get("unitBlock"),
            "sub_block_key": p.get("subBlockKey"),
            "offset_m": p.get("offsetInMeters"),
        })
    return out


def is_alley(c: dict) -> bool:
    return ("alley" in str(c["route_type"] or "").lower()
            or re.search(r"\bALLEY\b", str(c["route_name"] or "").upper()) is not None)


def permit_street(address: str | None) -> str:
    text = str(address or "").upper()
    return re.sub(r"^.*?\bBLOCK OF\s+", "", text).strip()


def street_matches(candidate: dict, address: str | None) -> bool:
    name = str(candidate["street_name"] or "").upper().strip()
    if not name:
        return False
    return re.search(rf"\b{re.escape(name)}\b", permit_street(address)) is not None


def choose_candidate(cands: list[dict], address: str | None):
    """Drop alleys / class-less hits, then take the nearest candidate. Street-name
    agreement with the permit address only breaks ties within TIE_TOLERANCE_M
    (e.g. at an intersection); it is otherwise reported as a QA flag."""
    usable = [c for c in cands if not is_alley(c) and c["fc_code"] is not None]
    if not usable:
        return None, ("no_match" if not cands else "alley_or_no_class")
    for c in usable:
        c["street_name_match"] = street_matches(c, address)
        c["_off"] = float(c["offset_m"] or 0)
    nearest = min(c["_off"] for c in usable)
    tied = [c for c in usable if c["_off"] <= nearest + TIE_TOLERANCE_M]
    tied.sort(key=lambda c: (not c["street_name_match"], c["_off"]))
    return tied[0], "ok"


def _to_state_plane(lon: float, lat: float):
    global _transformer
    if Transformer is None:
        return None
    if _transformer is None:
        _transformer = Transformer.from_crs(4326, STATE_PLANE_WKID, always_xy=True)
    return _transformer.transform(lon, lat)


def lookup_location(task: dict, search_radius: float) -> dict:
    result = {c: "" for c in CACHE_COLS}
    result["lookup_key"] = task["lookup_key"]

    attempts = []
    x, y = task.get("OnSegX"), task.get("OnSegY")
    if pd.notna(x) and pd.notna(y) and float(x) > 0 and float(y) > 0:
        attempts.append((float(x), float(y), "OnSeg"))
    lon, lat = task.get("Longitude"), task.get("Latitude")
    if pd.notna(lon) and pd.notna(lat):
        pt = _to_state_plane(float(lon), float(lat))
        if pt:
            attempts.append((pt[0], pt[1], "LatLon"))
    if not attempts:
        result["lookup_status"] = "no_coordinates"
        return result

    status, last_error = "no_match", ""
    for x, y, source in attempts:
        params = {
            "x": x, "y": y, "inSR": STATE_PLANE_WKID, "outSR": STATE_PLANE_WKID,
            "searchRadius": search_radius,
            "returnNearestMatchOnly": "false",
            "includeSectionEvents": "false",
            "f": "json",
        }
        if ROUTE_TYPE:
            params["routeType"] = ROUTE_TYPE
        try:
            payload = get_json(LRS_URL, params)
        except ServiceError as exc:
            status, last_error = "error", str(exc)[:300]
            continue
        cands = extract_candidates(payload)
        chosen, status = choose_candidate(cands, task.get("WorkLocationFullAddress"))
        result["n_candidates"] = len(cands)
        result["coord_source"] = source
        if chosen:
            result.update({k: chosen.get(k, "") for k in (
                "fc_code", "fc_label", "route_name", "route_type", "street_name",
                "unit_block", "sub_block_key", "offset_m", "street_name_match")})
            result["lookup_status"] = "ok"
            return result
    result["lookup_status"] = status
    result["error"] = last_error
    return result


# --------------------------------------------------------------------------- #
# Cache (so an interrupted run can resume)
# --------------------------------------------------------------------------- #
def load_cache(path: Path) -> dict:
    cache = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                cache[row["lookup_key"]] = row     # later rows override earlier ones
    return cache


def run_lookups(tasks: list[dict], cache: dict, cache_path: Path,
                workers: int, search_radius: float) -> None:
    todo = [t for t in tasks
            if t["lookup_key"] not in cache
            or cache[t["lookup_key"]].get("lookup_status") in RETRY_STATUSES]
    log.info("Locations: %d total, %d cached, %d to look up",
             len(tasks), len(tasks) - len(todo), len(todo))
    if not todo:
        return

    new_file = not cache_path.exists()
    with cache_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CACHE_COLS)
        if new_file:
            writer.writeheader()
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(lookup_location, t, search_radius) for t in todo]
            for fut in as_completed(futures):
                row = fut.result()
                writer.writerow(row)
                cache[row["lookup_key"]] = row
                done += 1
                if done % 50 == 0:
                    fh.flush()
                if done % 100 == 0 or done == len(todo):
                    log.info("Lookups: %d / %d", done, len(todo))


# --------------------------------------------------------------------------- #
# Step 4: merge + highest functional class per permit application
# --------------------------------------------------------------------------- #
def assemble(df: pd.DataFrame, cache: dict) -> pd.DataFrame:
    lk = pd.DataFrame(list(cache.values()), columns=CACHE_COLS)
    lk = lk.replace("", pd.NA)
    lk["fc_code"] = pd.to_numeric(lk["fc_code"], errors="coerce")
    lk["offset_m"] = pd.to_numeric(lk["offset_m"], errors="coerce").round(2)
    lk["street_name_match"] = lk["street_name_match"].map(
        {"True": True, "False": False, True: True, False: False})
    lk = lk.drop_duplicates("lookup_key", keep="last")

    m = df.merge(lk, on="lookup_key", how="left")
    m["lookup_status"] = m["lookup_status"].fillna("not_attempted")

    # Lower FHWA code = higher (more major) class, so "highest" is the minimum.
    resolved = m[m["fc_code"].notna()]
    idx = resolved.groupby("TrackingNumber")["fc_code"].idxmin()
    hi = resolved.loc[idx, ["TrackingNumber", "fc_code", "fc_label",
                            "WorkLocationFullAddress"]]
    hi.columns = ["TrackingNumber", "permit_highest_functional_class_code",
                  "permit_highest_functional_class", "permit_highest_fc_location"]

    counts = m.groupby("TrackingNumber").agg(
        permit_location_count=("lookup_key", "size"),
        permit_locations_resolved=("fc_code", "count"))
    counts["permit_highest_fc_complete"] = (
        counts["permit_location_count"] == counts["permit_locations_resolved"])

    m = m.merge(counts.reset_index(), on="TrackingNumber", how="left")
    m = m.merge(hi, on="TrackingNumber", how="left")

    m = m.rename(columns={
        "fc_code": "location_functional_class_code",
        "fc_label": "location_functional_class",
        "route_name": "lrs_route_name", "route_type": "lrs_route_type",
        "unit_block": "lrs_unit_block", "sub_block_key": "lrs_sub_block_key",
        "offset_m": "lrs_offset_m",
    })
    for col in ["location_functional_class_code",
                "permit_highest_functional_class_code", "LocationID"]:
        m[col] = pd.to_numeric(m[col], errors="coerce").astype("Int64")

    cols = [
        "TrackingNumber", "PermitNumber", "StatusDescription",
        "EventTypeDescription", "utility", "utility_matched_on", "ApplicantCompany",
        "ApplicationDate", "IssueDate", "EffectiveDate", "ExpirationDate",
        "LocationID", "WorkLocationFullAddress", "WorkLocationWard",
        "OnSegX", "OnSegY", "Latitude", "Longitude",
        "location_functional_class", "location_functional_class_code",
        "lrs_route_name", "lrs_route_type", "lrs_unit_block", "lrs_sub_block_key",
        "lrs_offset_m", "street_name_match", "lookup_status",
        "permit_location_count", "permit_locations_resolved",
        "permit_highest_functional_class", "permit_highest_functional_class_code",
        "permit_highest_fc_location", "permit_highest_fc_complete",
    ]
    return m[cols].sort_values(["TrackingNumber", "LocationID"]).reset_index(drop=True)


def summarize(out: pd.DataFrame, output_dir: Path) -> None:
    log.info("---- SUMMARY ----")
    log.info("Rows (permit locations): %d | permit applications: %d",
             len(out), out["TrackingNumber"].nunique())
    log.info("Lookup status:\n%s", out["lookup_status"].value_counts().to_string())
    pairs = (out.dropna(subset=["location_functional_class_code"])
             .groupby(["location_functional_class_code", "location_functional_class"])
             .size().reset_index(name="locations").sort_values(
                 "location_functional_class_code"))
    log.info("Functional classes observed (lower code = higher class):\n%s",
             pairs.to_string(index=False))
    pairs.to_csv(output_dir / "functional_class_codes_observed.csv", index=False)
    mism = (out["street_name_match"] == False).sum()  # noqa: E712
    log.info("Locations where LRS street name did not match the permit address "
             "(review these): %d", mism)
    incomplete = out.loc[~out["permit_highest_fc_complete"].astype(bool),
                         "TrackingNumber"].nunique()
    log.info("Permit applications with at least one unresolved location "
             "(highest class may be understated): %d", incomplete)


# --------------------------------------------------------------------------- #
def parse_args():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start-date", default=START_DATE, help="YYYY-MM-DD")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--search-radius", type=float, default=SEARCH_RADIUS)
    ap.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    ap.add_argument("--refresh", action="store_true",
                    help="ignore cached roadway lookups and redo them all")
    ap.add_argument("--limit", type=int, default=0,
                    help="only look up the first N locations (quick test run)")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(args.output_dir / "enrichment.log",
                                      mode="w", encoding="utf-8")])

    raw = fetch_permits(args.start_date)
    if raw.empty:
        log.error("No permit records returned; nothing to do.")
        return 1
    raw.to_csv(CACHE_DIR / "permits_raw_pull.csv", index=False, encoding="utf-8")

    df = prepare_permits(raw)
    tasks = (df.drop_duplicates("lookup_key")
             [["lookup_key", "OnSegX", "OnSegY", "Longitude", "Latitude",
               "WorkLocationFullAddress"]].to_dict("records"))
    name = OUTPUT_NAME
    if args.limit:
        tasks = tasks[:args.limit]
        df = df[df["lookup_key"].isin({t["lookup_key"] for t in tasks})]
        name = OUTPUT_NAME.replace(".csv", "_SAMPLE.csv")

    cache_path = CACHE_DIR / "lrs_lookup_cache.csv"
    if args.refresh and cache_path.exists():
        cache_path.unlink()
    cache = load_cache(cache_path)
    run_lookups(tasks, cache, cache_path, args.workers, args.search_radius)

    out = assemble(df, cache)
    out_path = args.output_dir / name
    out.to_csv(out_path, index=False, encoding="utf-8-sig")   # opens cleanly in Excel
    summarize(out, args.output_dir)
    log.info("Wrote %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
