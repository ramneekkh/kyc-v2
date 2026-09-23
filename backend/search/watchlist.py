"""
Sanctions & Watchlist Screening Engine (MAS, OFAC SDN, UN Security Council, EU Consolidated).

Adverse media search is open-source news discovery; it is NOT watchlist screening.
A regulated financial institution (MAS Notice 626, FATF Recommendations 6, 7 & 10,
31 CFR Part 501, EU Sanctions Regulations) must screen every prospective and
existing customer against structured designations issued by competent authorities.

This module ingests and indexes four live, official sanctions datasets:
  1. SG_MAS      - Monetary Authority of Singapore Targeted Financial Sanctions
                   (TSOFA First Schedule & UN Act designations)
  2. US_OFAC_SDN - U.S. Department of the Treasury OFAC Specially Designated
                   Nationals and Blocked Persons (SDN) List
  3. UN_SC       - United Nations Security Council Consolidated Sanctions List
  4. EU_FSF      - European Union Consolidated Financial Sanctions List

Design invariants:
  - Zero mock data: every entity indexed comes from the live authority feed (or
    a cryptographically hashed GCS cache of that feed).
  - Fail-closed coverage (`WatchlistCoverage`): if any mandatory list cannot be
    loaded or contains 0 entities, `WatchlistCoverage.is_complete` is False and
    the overall KYC summary refuses to emit a clean verdict.
  - Token-order-invariant, diacritic-insensitive, phonetic + fuzzy matching with
    secondary identifier corroboration (Date of Birth and Country/Jurisdiction).
"""

from __future__ import annotations

import csv
import datetime
from dataclasses import dataclass, field
import difflib
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Authoritative Sanctions Feed Definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WatchlistFeedSpec:
    list_id: str
    short_name: str
    authority: str
    jurisdiction: str
    primary_url: str
    fallback_url: Optional[str] = None
    mandatory: bool = True


WATCHLIST_FEEDS: Tuple[WatchlistFeedSpec, ...] = (
    WatchlistFeedSpec(
        list_id="SG_MAS",
        short_name="MAS Targeted Financial Sanctions",
        authority="Monetary Authority of Singapore (TSOFA / UN Act)",
        jurisdiction="SG",
        primary_url="https://data.opensanctions.org/datasets/latest/sg_terrorists/targets.simple.csv",
        mandatory=True,
    ),
    WatchlistFeedSpec(
        list_id="UN_SC",
        short_name="UN Security Council Consolidated List",
        authority="United Nations Security Council",
        jurisdiction="UN",
        primary_url="https://data.opensanctions.org/datasets/latest/un_sc_sanctions/targets.simple.csv",
        mandatory=True,
    ),
    WatchlistFeedSpec(
        list_id="EU_FSF",
        short_name="EU Consolidated Financial Sanctions",
        authority="European External Action Service / DG FISMA",
        jurisdiction="EU",
        primary_url="https://data.opensanctions.org/datasets/latest/eu_fsf/targets.simple.csv",
        mandatory=True,
    ),
    WatchlistFeedSpec(
        list_id="US_OFAC_SDN",
        short_name="US OFAC SDN List",
        authority="U.S. Department of the Treasury (OFAC)",
        jurisdiction="US",
        primary_url="https://data.opensanctions.org/datasets/latest/us_ofac_sdn/targets.simple.csv",
        fallback_url="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV",
        mandatory=True,
    ),
)

# Cache freshness ceiling (hours). If a cached list is older than this and live
# refresh fails, WatchlistCoverage marks the list as stale/failed.
MAX_WATCHLIST_AGE_HOURS = int(os.environ.get("KYC_WATCHLIST_MAX_AGE_HOURS", "48"))
IN_MEMORY_REFRESH_SECONDS = int(os.environ.get("KYC_WATCHLIST_REFRESH_SECONDS", str(6 * 3600)))

# Match score thresholds.
CONFIRMED_MATCH_THRESHOLD = 0.92
STRONG_MATCH_THRESHOLD = 0.86
POTENTIAL_MATCH_THRESHOLD = 0.80

# Corporate suffixes stripped during normalization so "Promsvyazbank PJSC" matches
# "Promsvyazbank Public Joint Stock Company".
_LEGAL_SUFFIX_RE = re.compile(
    r"\b("
    r"public joint stock company|joint stock company|private limited|pte ltd|"
    r"sdn bhd|bhd|pjsc|ojsc|jsc|llc|llp|inc|incorporated|corp|corporation|"
    r"co ltd|company limited|limited|ltd|plc|gmbh|ag|sa|sarl|bv|nv"
    r")\b",
    re.IGNORECASE,
)

# Common stopwords that should not drive single-token candidate retrieval.
_STOP_TOKENS: Set[str] = {
    "the", "and", "of", "for", "in", "to", "al", "bin", "binti", "ibn", "abu",
    "bank", "group", "holding", "holdings", "international", "trading",
    "company", "enterprise", "enterprises", "commercial", "general", "national",
    "united", "state", "central", "industrial", "development", "investment",
}


def normalize_watchlist_name(raw: str) -> str:
    """Normalizes a person or entity name for sanctions comparison."""
    if not raw:
        return ""
    # Strip parenthetical notes e.g. "KIM, Jong Un (a.k.a. ...)"
    cleaned = re.sub(r"\([^)]*\)", " ", str(raw))
    # Unicode NFKD + strip combining diacritics
    decomposed = unicodedata.normalize("NFKD", cleaned)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.casefold()
    # Replace punctuation with spaces
    alnum = re.sub(r"[^\w\s]", " ", lowered)
    without_suffix = _LEGAL_SUFFIX_RE.sub(" ", alnum)
    tokens = [t for t in without_suffix.split() if t]
    return " ".join(tokens)


def phonetic_key(token: str) -> str:
    """
    Produces a Consonant/Soundex-style phonetic skeleton for transliterated names
    (e.g. 'mohammed' vs 'muhammad', 'sergei' vs 'sergey', 'qaddafi' vs 'gaddafi').
    """
    if not token:
        return ""
    t = token.casefold()
    # Normalize common transliteration equivalences before consonant extraction
    t = (
        t.replace("ph", "f")
        .replace("kh", "k")
        .replace("gh", "k")
        .replace("dh", "d")
        .replace("zh", "j")
        .replace("sh", "s")
        .replace("ch", "c")
        .replace("ts", "s")
        .replace("tz", "s")
        .replace("qu", "k")
        .replace("q", "k")
        .replace("g", "k")
        .replace("x", "ks")
        .replace("y", "i")
        .replace("w", "v")
    )
    if not t:
        return ""
    first = t[0]
    # Keep first character, strip vowels from the tail, collapse repeats
    tail_chars = []
    prev = ""
    for ch in t[1:]:
        if ch in "aeiou":
            continue
        if ch != prev:
            tail_chars.append(ch)
            prev = ch
    return (first + "".join(tail_chars))[:8]


def _extract_birth_years(dob_field: str) -> Set[int]:
    """Extracts 4-digit birth years (1900-2025) from a free-form DOB string."""
    if not dob_field:
        return set()
    years: Set[int] = set()
    for m in re.finditer(r"\b(19\d{2}|20[0-2]\d)\b", str(dob_field)):
        yr = int(m.group(1))
        if 1900 <= yr <= 2025:
            years.add(yr)
    return years


def _extract_exact_dates(dob_field: str) -> Set[str]:
    """Extracts ISO YYYY-MM-DD dates where present."""
    if not dob_field:
        return set()
    return set(re.findall(r"\b(?:19\d{2}|20[0-2]\d)-\d{2}-\d{2}\b", str(dob_field)))


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class WatchlistEntry:
    entity_id: str
    list_id: str
    list_name: str
    authority: str
    schema_type: str  # Person, Organization, LegalEntity, Vessel, Aircraft
    primary_name: str
    aliases: List[str]
    normalized_names: List[Tuple[str, Tuple[str, ...], str]]  # (norm_str, sorted_tokens, original_display)
    birth_dates: str
    birth_years: Set[int]
    exact_dobs: Set[str]
    countries: List[str]
    addresses: str
    identifiers: str
    programs: str
    sanctions_notes: str
    first_seen: str
    last_seen: str


@dataclass
class WatchlistListStatus:
    list_id: str
    short_name: str
    authority: str
    jurisdiction: str
    loaded: bool
    entity_count: int
    fetched_at: Optional[str] = None
    source_sha256: Optional[str] = None
    source_origin: str = "none"  # "live", "gcs_cache", "error"
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "list_id": self.list_id,
            "short_name": self.short_name,
            "authority": self.authority,
            "jurisdiction": self.jurisdiction,
            "loaded": self.loaded,
            "entity_count": self.entity_count,
            "fetched_at": self.fetched_at,
            "source_sha256": self.source_sha256,
            "source_origin": self.source_origin,
            "error": self.error,
        }


@dataclass
class WatchlistCoverage:
    lists_attempted: int = 0
    lists_succeeded: int = 0
    total_entities_indexed: int = 0
    list_statuses: List[WatchlistListStatus] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def lists_failed(self) -> int:
        return max(0, self.lists_attempted - self.lists_succeeded)

    @property
    def is_complete(self) -> bool:
        if self.lists_attempted == 0:
            return False
        return self.lists_succeeded == self.lists_attempted and self.total_entities_indexed > 0

    def describe(self) -> str:
        if self.lists_attempted == 0:
            return "No sanctions watchlists were queried."
        names = ", ".join(
            f"{s.list_id} ({s.entity_count:,})" if s.loaded else f"{s.list_id} (FAILED)"
            for s in self.list_statuses
        )
        return (
            f"{self.lists_succeeded}/{self.lists_attempted} sanctions lists active "
            f"({self.total_entities_indexed:,} designated entities indexed: {names})."
        )

    def to_dict(self) -> Dict:
        return {
            "lists_attempted": self.lists_attempted,
            "lists_succeeded": self.lists_succeeded,
            "lists_failed": self.lists_failed,
            "total_entities_indexed": self.total_entities_indexed,
            "is_complete": self.is_complete,
            "description": self.describe(),
            "lists": [s.to_dict() for s in self.list_statuses],
            "errors": self.errors[:5],
        }


@dataclass
class WatchlistHit:
    hit_id: str
    list_id: str
    list_name: str
    authority: str
    entity_id: str
    schema_type: str
    primary_name: str
    matched_name: str
    queried_name: str
    queried_role: str  # "subject", "alias", "related_party"
    match_score: float
    match_strength: str  # "CONFIRMED", "STRONG", "POTENTIAL"
    dob_corroboration: str  # "EXACT", "YEAR_MATCH", "MISMATCH", "UNAVAILABLE"
    country_corroboration: str  # "MATCH", "NO_OVERLAP", "UNAVAILABLE"
    birth_date: str
    countries: List[str]
    addresses: str
    identifiers: str
    programs: str
    sanctions_notes: str
    first_seen: str
    last_seen: str
    explanation: str

    def to_dict(self) -> Dict:
        return {
            "hit_id": self.hit_id,
            "list_id": self.list_id,
            "list_name": self.list_name,
            "authority": self.authority,
            "entity_id": self.entity_id,
            "schema_type": self.schema_type,
            "primary_name": self.primary_name,
            "matched_name": self.matched_name,
            "queried_name": self.queried_name,
            "queried_role": self.queried_role,
            "match_score": round(self.match_score, 4),
            "match_strength": self.match_strength,
            "dob_corroboration": self.dob_corroboration,
            "country_corroboration": self.country_corroboration,
            "birth_date": self.birth_date,
            "countries": self.countries,
            "addresses": self.addresses,
            "identifiers": self.identifiers,
            "programs": self.programs,
            "sanctions_notes": self.sanctions_notes,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "explanation": self.explanation,
        }


# ---------------------------------------------------------------------------
# Watchlist Index & Screening Engine
# ---------------------------------------------------------------------------

class WatchlistIndex:
    """
    Thread-safe in-memory inverted index over SG_MAS, UN_SC, EU_FSF, and US_OFAC_SDN.
    Backed by live HTTP ingestion and GCS snapshot caching.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._entries: List[WatchlistEntry] = []
        self._by_token: Dict[str, List[int]] = {}
        self._by_phonetic: Dict[str, List[int]] = {}
        self._by_exact_norm: Dict[str, List[int]] = {}
        self._coverage: WatchlistCoverage = WatchlistCoverage()
        self._loaded_at: float = 0.0

    def ensure_loaded(self, force_refresh: bool = False) -> WatchlistCoverage:
        now = time.time()
        with self._lock:
            if (
                not force_refresh
                and self._entries
                and self._coverage.is_complete
                and (now - self._loaded_at) < IN_MEMORY_REFRESH_SECONDS
            ):
                return self._coverage
            return self._reload_all_lists()

    def get_status(self) -> Dict:
        cov = self.ensure_loaded(force_refresh=False)
        return cov.to_dict()

    def _reload_all_lists(self) -> WatchlistCoverage:
        entries: List[WatchlistEntry] = []
        statuses: List[WatchlistListStatus] = []
        errors: List[str] = []
        succeeded = 0

        for spec in WATCHLIST_FEEDS:
            status, list_entries = self._load_single_feed(spec)
            statuses.append(status)
            if status.loaded and list_entries:
                succeeded += 1
                entries.extend(list_entries)
            else:
                err_msg = f"{spec.list_id}: {status.error or '0 entities parsed'}"
                errors.append(err_msg)
                logger.error(f"Watchlist load failure: {err_msg}")

        # Build fast lookup structures
        by_token: Dict[str, List[int]] = {}
        by_phonetic: Dict[str, List[int]] = {}
        by_exact_norm: Dict[str, List[int]] = {}

        for idx, entry in enumerate(entries):
            seen_tokens: Set[str] = set()
            seen_phonetics: Set[str] = set()
            Seen_norms: Set[str] = set()
            for norm_str, sorted_tokens, _ in entry.normalized_names:
                if norm_str and norm_str not in Seen_norms:
                    Seen_norms.add(norm_str)
                    by_exact_norm.setdefault(norm_str, []).append(idx)
                for tok in sorted_tokens:
                    if len(tok) < 2 or tok in _STOP_TOKENS:
                        continue
                    if tok not in seen_tokens:
                        seen_tokens.add(tok)
                        by_token.setdefault(tok, []).append(idx)
                    pk = phonetic_key(tok)
                    if len(pk) >= 2 and pk not in seen_phonetics:
                        seen_phonetics.add(pk)
                        by_phonetic.setdefault(pk, []).append(idx)

        self._entries = entries
        self._by_token = by_token
        self._by_phonetic = by_phonetic
        self._by_exact_norm = by_exact_norm
        self._loaded_at = time.time()
        self._coverage = WatchlistCoverage(
            lists_attempted=len(WATCHLIST_FEEDS),
            lists_succeeded=succeeded,
            total_entities_indexed=len(entries),
            list_statuses=statuses,
            errors=errors,
        )
        logger.info(f"WatchlistIndex ready: {self._coverage.describe()}")
        return self._coverage

    def _load_single_feed(
        self, spec: WatchlistFeedSpec
    ) -> Tuple[WatchlistListStatus, List[WatchlistEntry]]:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        raw_csv: Optional[str] = None
        origin = "none"
        last_err: Optional[str] = None
        used_fallback_format = False

        # 1. Try primary live URL
        try:
            resp = requests.get(spec.primary_url, timeout=25)
            resp.raise_for_status()
            if len(resp.content) > 200:
                raw_csv = resp.content.decode("utf-8", errors="replace")
                origin = "live"
                self._save_to_gcs_cache(spec.list_id, raw_csv, now_iso)
        except Exception as e:
            last_err = f"primary URL failed ({e})"
            logger.warning(f"Watchlist {spec.list_id} primary fetch failed: {e}")

        # 2. Try fallback live URL (e.g., direct US Treasury SDN.CSV)
        if not raw_csv and spec.fallback_url:
            try:
                resp = requests.get(spec.fallback_url, timeout=30)
                resp.raise_for_status()
                if len(resp.content) > 200:
                    raw_csv = resp.content.decode("utf-8", errors="replace")
                    origin = "live_fallback"
                    used_fallback_format = "SDN.CSV" in spec.fallback_url.upper()
                    self._save_to_gcs_cache(spec.list_id, raw_csv, now_iso)
            except Exception as e:
                last_err = f"{last_err}; fallback URL failed ({e})"
                logger.warning(f"Watchlist {spec.list_id} fallback fetch failed: {e}")

        # 3. Try GCS snapshot cache if live download failed
        if not raw_csv:
            cached_csv, cached_ts = self._load_from_gcs_cache(spec.list_id)
            if cached_csv:
                raw_csv = cached_csv
                now_iso = cached_ts or now_iso
                origin = "gcs_cache"

        if not raw_csv:
            return (
                WatchlistListStatus(
                    list_id=spec.list_id,
                    short_name=spec.short_name,
                    authority=spec.authority,
                    jurisdiction=spec.jurisdiction,
                    loaded=False,
                    entity_count=0,
                    source_origin="error",
                    error=last_err or "Failed to fetch feed",
                ),
                [],
            )

        sha = hashlib.sha256(raw_csv.encode("utf-8", errors="ignore")).hexdigest()[:16]
        try:
            if used_fallback_format:
                parsed = self._parse_treasury_sdn_csv(spec, raw_csv)
            else:
                parsed = self._parse_simple_targets_csv(spec, raw_csv)
        except Exception as e:
            return (
                WatchlistListStatus(
                    list_id=spec.list_id,
                    short_name=spec.short_name,
                    authority=spec.authority,
                    jurisdiction=spec.jurisdiction,
                    loaded=False,
                    entity_count=0,
                    source_origin="error",
                    error=f"CSV parse error: {e}",
                ),
                [],
            )

        return (
            WatchlistListStatus(
                list_id=spec.list_id,
                short_name=spec.short_name,
                authority=spec.authority,
                jurisdiction=spec.jurisdiction,
                loaded=len(parsed) > 0,
                entity_count=len(parsed),
                fetched_at=now_iso,
                source_sha256=sha,
                source_origin=origin,
                error=None if parsed else "Feed parsed 0 records",
            ),
            parsed,
        )

    def _save_to_gcs_cache(self, list_id: str, csv_text: str, fetched_at: str) -> None:
        try:
            from ..storage.gcs_client import gcs_client
            if gcs_client and gcs_client.bucket:
                gcs_client.upload_from_string(
                    csv_text,
                    f"watchlists/{list_id}/latest.csv",
                    content_type="text/csv",
                )
                gcs_client.upload_from_string(
                    json.dumps({"list_id": list_id, "fetched_at": fetched_at}),
                    f"watchlists/{list_id}/metadata.json",
                    content_type="application/json",
                )
        except Exception as e:
            logger.debug(f"GCS watchlist cache write skipped for {list_id}: {e}")

    def _load_from_gcs_cache(self, list_id: str) -> Tuple[Optional[str], Optional[str]]:
        try:
            from ..storage.gcs_client import gcs_client
            if not gcs_client or not gcs_client.bucket:
                return None, None
            meta_raw = gcs_client.download_as_string(f"watchlists/{list_id}/metadata.json")
            fetched_at = None
            if meta_raw:
                meta = json.loads(meta_raw)
                fetched_at = meta.get("fetched_at")
                if fetched_at:
                    dt = datetime.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                    age_hours = (
                        datetime.datetime.now(datetime.timezone.utc) - dt
                    ).total_seconds() / 3600.0
                    if age_hours > MAX_WATCHLIST_AGE_HOURS:
                        logger.warning(
                            f"GCS watchlist cache for {list_id} is {age_hours:.1f}h old "
                            f"(limit {MAX_WATCHLIST_AGE_HOURS}h); refusing stale cache."
                        )
                        return None, None
            csv_text = gcs_client.download_as_string(f"watchlists/{list_id}/latest.csv")
            return csv_text, fetched_at
        except Exception as e:
            logger.debug(f"GCS watchlist cache read missed for {list_id}: {e}")
            return None, None

    @staticmethod
    def _parse_simple_targets_csv(
        spec: WatchlistFeedSpec, csv_text: str
    ) -> List[WatchlistEntry]:
        entries: List[WatchlistEntry] = []
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            primary_name = (row.get("name") or "").strip()
            if not primary_name:
                continue
            aliases_raw = row.get("aliases") or ""
            aliases = [a.strip() for a in aliases_raw.split(";") if a.strip()]
            all_raw_names = [primary_name] + aliases
            normalized_names: List[Tuple[str, Tuple[str, ...], str]] = []
            seen_norm: Set[str] = set()
            for raw_n in all_raw_names:
                norm_n = normalize_watchlist_name(raw_n)
                if not norm_n or len(norm_n) < 3 or norm_n in seen_norm:
                    continue
                seen_norm.add(norm_n)
                toks = tuple(sorted(norm_n.split()))
                normalized_names.append((norm_n, toks, raw_n))
            if not normalized_names:
                continue

            dob_str = (row.get("birth_date") or "").strip()
            countries_raw = (row.get("countries") or "").strip()
            countries = [c.strip().upper() for c in countries_raw.split(";") if c.strip()]

            entries.append(
                WatchlistEntry(
                    entity_id=(row.get("id") or "").strip(),
                    list_id=spec.list_id,
                    list_name=spec.short_name,
                    authority=spec.authority,
                    schema_type=(row.get("schema") or "Entity").strip(),
                    primary_name=primary_name,
                    aliases=aliases[:25],
                    normalized_names=normalized_names,
                    birth_dates=dob_str,
                    birth_years=_extract_birth_years(dob_str),
                    exact_dobs=_extract_exact_dates(dob_str),
                    countries=countries,
                    addresses=(row.get("addresses") or "").strip(),
                    identifiers=(row.get("identifiers") or "").strip(),
                    programs=(row.get("program_ids") or row.get("dataset") or "").strip(),
                    sanctions_notes=(row.get("sanctions") or "").strip(),
                    first_seen=(row.get("first_seen") or "").strip(),
                    last_seen=(row.get("last_seen") or "").strip(),
                )
            )
        return entries

    @staticmethod
    def _parse_treasury_sdn_csv(
        spec: WatchlistFeedSpec, csv_text: str
    ) -> List[WatchlistEntry]:
        """Fallback parser for the raw headerless US Treasury SDN.CSV export."""
        entries: List[WatchlistEntry] = []
        reader = csv.reader(io.StringIO(csv_text))
        for row in reader:
            if len(row) < 4:
                continue
            ent_num = row[0].strip()
            sdn_name = row[1].strip()
            if not sdn_name or ent_num == "\x1a":
                continue
            sdn_type = row[2].strip() or "Entity"
            program = row[3].strip()
            remarks = row[11].strip() if len(row) > 11 else ""
            norm_n = normalize_watchlist_name(sdn_name)
            if not norm_n or len(norm_n) < 3:
                continue
            toks = tuple(sorted(norm_n.split()))
            entries.append(
                WatchlistEntry(
                    entity_id=f"OFAC-{ent_num}",
                    list_id=spec.list_id,
                    list_name=spec.short_name,
                    authority=spec.authority,
                    schema_type="Person" if "individual" in sdn_type.lower() else "Organization",
                    primary_name=sdn_name,
                    aliases=[],
                    normalized_names=[(norm_n, toks, sdn_name)],
                    birth_dates=remarks,
                    birth_years=_extract_birth_years(remarks),
                    exact_dobs=_extract_exact_dates(remarks),
                    countries=[],
                    addresses="",
                    identifiers=remarks,
                    programs=program,
                    sanctions_notes=remarks,
                    first_seen="",
                    last_seen="",
                )
            )
        return entries

    # -----------------------------------------------------------------------
    # Candidate Retrieval & Multi-Factor Scoring
    # -----------------------------------------------------------------------

    def screen(
        self,
        subject_name: str,
        aliases: Optional[List[str]] = None,
        dob: Optional[str] = None,
        age: Optional[str] = None,
        region: Optional[str] = None,
        related_parties: Optional[List[Tuple[str, str]]] = None,
    ) -> Tuple[List[WatchlistHit], WatchlistCoverage]:
        """
        Screens a subject (plus aliases and optional related parties) against all
        loaded watchlists. Returns `(hits, coverage)`.
        """
        coverage = self.ensure_loaded(force_refresh=False)
        if not subject_name or not subject_name.strip():
            return [], coverage

        # Derive subject birth years & exact DOBs for secondary corroboration
        subject_years: Set[int] = _extract_birth_years(dob or "")
        subject_exact_dobs: Set[str] = _extract_exact_dates(dob or "")
        if not subject_years and age:
            try:
                age_int = int(re.search(r"\d+", str(age)).group())
                if 15 <= age_int <= 110:
                    curr_year = datetime.datetime.now(datetime.timezone.utc).year
                    subject_years.add(curr_year - age_int)
            except Exception:
                pass

        subject_country_tokens = self._parse_country_hints(region or "")

        queries: List[Tuple[str, str]] = [(subject_name.strip(), "subject")]
        for a in (aliases or []):
            if a and a.strip() and a.strip().lower() != subject_name.strip().lower():
                queries.append((a.strip(), "alias"))
        for rp_name, rp_role in (related_parties or []):
            if rp_name and rp_name.strip():
                queries.append((rp_name.strip(), rp_role))

        hits_by_key: Dict[Tuple[str, str], WatchlistHit] = {}

        with self._lock:
            for q_name, q_role in queries:
                q_norm = normalize_watchlist_name(q_name)
                if not q_norm or len(q_norm) < 3:
                    continue
                q_tokens = [t for t in q_norm.split() if len(t) >= 2]
                if not q_tokens:
                    continue
                q_sorted_tokens = tuple(sorted(q_tokens))

                # Candidate retrieval via exact norm, token index, and phonetic index
                candidate_indices: Set[int] = set(self._by_exact_norm.get(q_norm, []))
                for tok in q_tokens:
                    if tok in _STOP_TOKENS and len(q_tokens) > 1:
                        continue
                    for idx in self._by_token.get(tok, []):
                        candidate_indices.add(idx)
                    pk = phonetic_key(tok)
                    if len(pk) >= 3:
                        for idx in self._by_phonetic.get(pk, []):
                            candidate_indices.add(idx)

                for idx in candidate_indices:
                    entry = self._entries[idx]
                    best_score = 0.0
                    best_matched_display = entry.primary_name
                    for e_norm, e_sorted_tokens, e_display in entry.normalized_names:
                        sc = self._compute_name_similarity(
                            q_norm, q_sorted_tokens, e_norm, e_sorted_tokens
                        )
                        if sc > best_score:
                            best_score = sc
                            best_matched_display = e_display

                    if best_score < POTENTIAL_MATCH_THRESHOLD:
                        continue

                    # Apply secondary identifier corroboration (DOB & Country)
                    # Note: DOB corroboration only applies to the subject/alias, not a related company/spouse
                    if q_role in ("subject", "alias"):
                        dob_status, score_delta = self._evaluate_dob(
                            subject_exact_dobs, subject_years, entry.exact_dobs, entry.birth_years
                        )
                    else:
                        dob_status, score_delta = "UNAVAILABLE", 0.0

                    country_status, c_delta = self._evaluate_country(
                        subject_country_tokens, entry.countries, entry.addresses
                    )

                    adjusted_score = max(0.0, min(1.0, best_score + score_delta + c_delta))
                    if adjusted_score < POTENTIAL_MATCH_THRESHOLD:
                        continue

                    # Determine match strength
                    if dob_status == "MISMATCH":
                        # A conflicting birth year (>2 years apart) downgrades an otherwise
                        # strong name match to POTENTIAL so a human analyst verifies ID docs.
                        strength = "POTENTIAL"
                    elif adjusted_score >= CONFIRMED_MATCH_THRESHOLD:
                        strength = "CONFIRMED"
                    elif adjusted_score >= STRONG_MATCH_THRESHOLD:
                        strength = "STRONG"
                    else:
                        strength = "POTENTIAL"

                    explanation = self._build_hit_explanation(
                        q_name=q_name,
                        q_role=q_role,
                        entry=entry,
                        matched_display=best_matched_display,
                        name_score=best_score,
                        adjusted_score=adjusted_score,
                        strength=strength,
                        dob_status=dob_status,
                        country_status=country_status,
                    )

                    dedup_key = (entry.list_id, entry.entity_id)
                    existing = hits_by_key.get(dedup_key)
                    if existing is None or adjusted_score > existing.match_score:
                        hit_id = str(
                            uuid.uuid5(
                                uuid.NAMESPACE_OID,
                                f"watchlist:{entry.list_id}:{entry.entity_id}:{q_norm}",
                            )
                        )
                        hits_by_key[dedup_key] = WatchlistHit(
                            hit_id=hit_id,
                            list_id=entry.list_id,
                            list_name=entry.list_name,
                            authority=entry.authority,
                            entity_id=entry.entity_id,
                            schema_type=entry.schema_type,
                            primary_name=entry.primary_name,
                            matched_name=best_matched_display,
                            queried_name=q_name,
                            queried_role=q_role,
                            match_score=adjusted_score,
                            match_strength=strength,
                            dob_corroboration=dob_status,
                            country_corroboration=country_status,
                            birth_date=entry.birth_dates,
                            countries=entry.countries,
                            addresses=entry.addresses,
                            identifiers=entry.identifiers,
                            programs=entry.programs,
                            sanctions_notes=entry.sanctions_notes,
                            first_seen=entry.first_seen,
                            last_seen=entry.last_seen,
                            explanation=explanation,
                        )

        sorted_hits = sorted(
            hits_by_key.values(),
            key=lambda h: (
                {"CONFIRMED": 3, "STRONG": 2, "POTENTIAL": 1}.get(h.match_strength, 0),
                h.match_score,
            ),
            reverse=True,
        )
        return sorted_hits, coverage

    @staticmethod
    def _compute_name_similarity(
        q_norm: str,
        q_sorted_tokens: Tuple[str, ...],
        e_norm: str,
        e_sorted_tokens: Tuple[str, ...],
    ) -> float:
        if q_norm == e_norm:
            return 1.0
        if q_sorted_tokens == e_sorted_tokens:
            # Exact same tokens in a different order (e.g. "ONG Beng Seng" vs "Beng Seng ONG")
            return 0.98

        # Guard against single-token queries matching multi-token names loosely
        if len(q_sorted_tokens) == 1 and len(e_sorted_tokens) > 1:
            return 0.0
        if len(e_sorted_tokens) == 1 and len(q_sorted_tokens) > 1:
            return 0.0

        # Token-level best-pair alignment (combines edit similarity + phonetic match)
        q_set = set(q_sorted_tokens)
        e_set = set(e_sorted_tokens)
        exact_overlap = len(q_set & e_set)
        min_len = min(len(q_sorted_tokens), len(e_sorted_tokens))
        max_len = max(len(q_sorted_tokens), len(e_sorted_tokens))

        # Require at least one high-similarity non-stop token
        pair_scores: List[float] = []
        for qt in q_sorted_tokens:
            best_t = 0.0
            q_pk = phonetic_key(qt)
            for et in e_sorted_tokens:
                if qt == et:
                    best_t = 1.0
                    break
                seq_r = difflib.SequenceMatcher(None, qt, et).ratio()
                if q_pk and q_pk == phonetic_key(et) and seq_r >= 0.72:
                    seq_r = max(seq_r, 0.92)
                if seq_r > best_t:
                    best_t = seq_r
            pair_scores.append(best_t)

        if not pair_scores or min(pair_scores) < 0.70:
            return 0.0

        token_coverage = sum(pair_scores) / len(pair_scores)
        length_penalty = (min_len / max_len) ** 0.5
        sorted_q_str = " ".join(q_sorted_tokens)
        sorted_e_str = " ".join(e_sorted_tokens)
        full_ratio = difflib.SequenceMatcher(None, sorted_q_str, sorted_e_str).ratio()

        composite = (0.60 * token_coverage * length_penalty) + (0.40 * full_ratio)
        if exact_overlap == min_len and min_len >= 2 and max_len - min_len == 1:
            # e.g. 2-token query ("Viktor Bout") inside 3-token full name ("Viktor Anatolyevich Bout")
            composite = max(composite, 0.91)
        return composite

    @staticmethod
    def _evaluate_dob(
        subject_dobs: Set[str],
        subject_years: Set[int],
        entry_dobs: Set[str],
        entry_years: Set[int],
    ) -> Tuple[str, float]:
        if not subject_years and not subject_dobs:
            return "UNAVAILABLE", 0.0
        if not entry_years and not entry_dobs:
            return "UNAVAILABLE", 0.0

        if subject_dobs and entry_dobs and (subject_dobs & entry_dobs):
            return "EXACT", 0.05

        if subject_years and entry_years:
            if subject_years & entry_years:
                return "YEAR_MATCH", 0.03
            min_diff = min(abs(sy - ey) for sy in subject_years for ey in entry_years)
            if min_diff <= 2:
                return "YEAR_NEAR", 0.01
            return "MISMATCH", -0.08

        return "UNAVAILABLE", 0.0

    @staticmethod
    def _parse_country_hints(region_str: str) -> Set[str]:
        if not region_str:
            return set()
        country_map = {
            "singapore": "SG", "sg": "SG",
            "malaysia": "MY", "my": "MY",
            "indonesia": "ID", "id": "ID",
            "russia": "RU", "russian": "RU", "ru": "RU",
            "iran": "IR", "iranian": "IR", "ir": "IR",
            "north korea": "KP", "dprk": "KP", "kp": "KP",
            "myanmar": "MM", "burma": "MM", "mm": "MM",
            "syria": "SY", "sy": "SY",
            "china": "CN", "cn": "CN",
            "hong kong": "HK", "hk": "HK",
            "united states": "US", "usa": "US", "us": "US",
            "united kingdom": "GB", "uk": "GB", "gb": "GB",
            "ukraine": "UA", "ua": "UA",
            "belarus": "BY", "by": "BY",
            "venezuela": "VE", "ve": "VE",
            "afghanistan": "AF", "af": "AF",
        }
        tokens: Set[str] = set()
        lowered = region_str.casefold()
        for name, iso in country_map.items():
            if re.search(rf"\b{re.escape(name)}\b", lowered):
                tokens.add(iso)
                tokens.add(name.upper())
        for part in re.split(r"[,;/]", region_str):
            p = part.strip().upper()
            if p:
                tokens.add(p)
        return tokens

    @staticmethod
    def _evaluate_country(
        subject_countries: Set[str],
        entry_countries: List[str],
        entry_addresses: str,
    ) -> Tuple[str, float]:
        if not subject_countries or (not entry_countries and not entry_addresses):
            return "UNAVAILABLE", 0.0
        entry_set = {c.upper() for c in entry_countries}
        addr_upper = (entry_addresses or "").upper()
        for sc in subject_countries:
            if sc in entry_set or (len(sc) > 3 and sc in addr_upper):
                return "MATCH", 0.02
        return "NO_OVERLAP", 0.0

    @staticmethod
    def _build_hit_explanation(
        q_name: str,
        q_role: str,
        entry: WatchlistEntry,
        matched_display: str,
        name_score: float,
        adjusted_score: float,
        strength: str,
        dob_status: str,
        country_status: str,
    ) -> str:
        role_label = {
            "subject": "Subject name",
            "alias": "Submitted alias",
        }.get(q_role, f"Related party ({q_role})")
        parts = [
            f"{role_label} '{q_name}' matched '{matched_display}' "
            f"(primary designation: '{entry.primary_name}') on {entry.list_name} "
            f"[{entry.entity_id}] with {adjusted_score * 100:.1f}% confidence ({strength})."
        ]
        if entry.programs:
            parts.append(f"Program(s): {entry.programs}.")
        if dob_status == "EXACT":
            parts.append(f"Corroborated by exact Date of Birth match ({entry.birth_dates}).")
        elif dob_status == "YEAR_MATCH":
            parts.append(f"Corroborated by birth year match ({entry.birth_dates}).")
        elif dob_status == "MISMATCH":
            parts.append(
                f"Watchlist DOB ({entry.birth_dates}) conflicts with submitted DOB; "
                f"downgraded to POTENTIAL pending identity document verification."
            )
        if country_status == "MATCH":
            parts.append(f"Jurisdiction overlap ({', '.join(entry.countries)}).")
        return " ".join(parts)


import uuid  # placed after class for hit_id uuid5 generation

watchlist_index = WatchlistIndex()


def screen_subject_watchlists(filters: Dict) -> Dict:
    """
    High-level entry point called by `run_kyc_process` and `/api/watchlist/screen`.
    Extracts subject, aliases, DOB, region, and related parties from `filters`
    and returns a structured dict ready for Spanner persistence and UI rendering.
    """
    subject_name = (filters.get("subject_name") or "").strip()
    alias_raw = filters.get("alias") or ""
    aliases = [
        a.strip()
        for a in re.split(r"[,;/]", str(alias_raw))
        if a and a.strip()
    ]
    related: List[Tuple[str, str]] = []
    for field_key, role_label in (
        ("company", "company"),
        ("ownership", "ownership_ubo"),
        ("spouse", "spouse"),
    ):
        val = (filters.get(field_key) or "").strip()
        if val:
            for item in re.split(r"[,;/]", val):
                if item.strip():
                    related.append((item.strip(), role_label))

    hits, coverage = watchlist_index.screen(
        subject_name=subject_name,
        aliases=aliases,
        dob=filters.get("dob"),
        age=filters.get("age"),
        region=filters.get("region"),
        related_parties=related,
    )

    confirmed_count = sum(1 for h in hits if h.match_strength == "CONFIRMED")
    strong_count = sum(1 for h in hits if h.match_strength == "STRONG")
    potential_count = sum(1 for h in hits if h.match_strength == "POTENTIAL")

    if not coverage.is_complete:
        status = "INCOMPLETE_COVERAGE"
    elif confirmed_count > 0 or strong_count > 0:
        status = "SANCTIONS_HIT_DETECTED"
    elif potential_count > 0:
        status = "POTENTIAL_MATCH_REVIEW_REQUIRED"
    else:
        status = "CLEAR"

    return {
        "status": status,
        "subject_name": subject_name,
        "total_hits": len(hits),
        "confirmed_hits": confirmed_count,
        "strong_hits": strong_count,
        "potential_hits": potential_count,
        "hits": [h.to_dict() for h in hits],
        "coverage": coverage.to_dict(),
        "screened_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
