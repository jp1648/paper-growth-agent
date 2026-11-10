from __future__ import annotations

import hashlib
import re
import threading
import time
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import httpx

from .models import dec, dt, dumps, news_time, now
from .observability import traced


class EvidenceTooLarge(ValueError):
    """The complete response cannot be retrieved within the evidence byte bound."""


class FilingText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "ix:hidden"}:
            self.hidden += 1
        if tag in {"p", "div", "tr", "td", "br"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "ix:hidden"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _recent_filings(recent, cutoff):
    forms = recent["form"]
    if (
        not isinstance(forms, list)
        or (forms and not {"accessionNumber", "acceptanceDateTime"}.issubset(recent))
        or any(not isinstance(v, list) or len(v) != len(forms) for v in recent.values())
        or any(not isinstance(form, str) or not form.strip() for form in forms)
    ):
        raise ValueError("SEC recent submissions are malformed")
    filings = []
    for i, form in enumerate(forms):
        if form.removesuffix("/A") not in {"10-K", "10-Q", "8-K", "20-F", "6-K"}:
            continue
        filing = {key: values[i] for key, values in recent.items()}
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", filing["accessionNumber"]):
            raise ValueError("SEC filing lacks valid accession")
        if dt(filing["acceptanceDateTime"]) > cutoff:
            raise ValueError("SEC filing acceptance is future-dated")
        filings.append(filing)
    return filings


class EvidenceProvider:
    def __init__(self, settings, store, data, client=None):
        self.cfg, self.store, self.data = settings, store, data
        self.client = client or httpx.Client(timeout=30, follow_redirects=False)
        self._lock = threading.Lock()
        self._last_request = 0.0

    @traced("evidence.request")
    def request(self, url):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"data.sec.gov", "www.sec.gov"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
        ):
            raise ValueError("Only HTTPS SEC evidence URLs are allowed")
        if not self.cfg.sec_user_agent or "@" not in self.cfg.sec_user_agent:
            raise ValueError("Set SEC user-agent application name and contact email")
        with self._lock:
            time.sleep(max(0, 0.2 - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
            with self.client.stream(
                "GET", url, headers={"User-Agent": self.cfg.sec_user_agent}, follow_redirects=False
            ) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 8_000_000:
                        raise EvidenceTooLarge("SEC document exceeds retrieval limit")
                    chunks.append(chunk)
                return httpx.Response(200, content=b"".join(chunks))

    @traced("evidence.get")
    def get(self, symbol, fresh=False):
        if symbol not in self.cfg.research_universe:
            raise ValueError("Symbol outside configured research universe")
        cached = self.store.get(symbol, "evidence_cache")
        if (
            cached
            and cached.get("sec_schema") == 7
            and not fresh
            and timedelta(0)
            <= now() - dt(cached["fetched_at"])
            < timedelta(hours=self.cfg.research_cache_hours)
        ):
            return cached
        bundle = self.fetch(symbol)
        self.store.put(symbol, bundle, "evidence_cache")
        return bundle

    def fetch(self, symbol):
        started_at = now()
        if symbol not in self.cfg.research_universe:
            raise ValueError("Symbol outside configured research universe")
        if not self.cfg.sec_user_agent or "@" not in self.cfg.sec_user_agent:
            raise ValueError("Set SEC user-agent application name and contact email")
        cik = self.cfg.research_universe[symbol].cik.zfill(10)
        sources = []
        tags = {
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "NetIncomeLoss",
            "WeightedAverageNumberOfDilutedSharesOutstanding",
            "NetCashProvidedByUsedInOperatingActivities",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "CashAndCashEquivalentsAtCarryingValue",
            "LongTermDebtCurrent",
            "LongTermDebtNoncurrent",
            "ShareBasedCompensation",
            "AllocatedShareBasedCompensationExpense",
            "PaymentsForRepurchaseOfCommonStock",
            "PaymentsOfDividendsCommonStock",
            "IncomeTaxesPaidNet",
            "StockholdersEquity",
            "CommonStockSharesOutstanding",
            "EntityCommonStockSharesOutstanding",
            "GrossProfit",
            "OperatingIncomeLoss",
        }
        for kind, url in (
            ("facts", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"),
            ("filings", f"https://data.sec.gov/submissions/CIK{cik}.json"),
        ):
            response = self.request(url)
            raw = response.json(parse_float=dec)
            if str(raw.get("cik", "")).zfill(10) != cik:
                raise ValueError("SEC response company identity does not match configured CIK")
            if kind == "facts":
                values = {}
                namespaces = {}
                for namespace, taxonomy in raw["facts"].items():
                    for tag, entry in taxonomy.items():
                        expected = "dei" if tag == "EntityCommonStockSharesOutstanding" else "us-gaap"
                        if tag in tags and namespace == expected:
                            namespaces[tag] = namespace
                            values[tag] = {
                                unit: sorted(
                                    [
                                        v
                                        for v in records
                                        if v.get("filed", "") < started_at.date().isoformat()
                                    ],
                                    key=lambda x: (x.get("filed", ""), x.get("end", "")),
                                    reverse=True,
                                )[:12]
                                for unit, records in entry["units"].items()
                            }
                if not values:
                    raise ValueError("SEC evidence lacks usable financial facts")
                content = {
                    "cik": cik,
                    "entity": raw["entityName"],
                    "facts": values,
                    "fact_taxonomies": namespaces,
                    "caution": "Date-only facts filed today are excluded because availability time is unknown. Facts may repeat/restated; compare matching fiscal durations and units; do not sum overlapping YTD periods.",
                }
            else:
                recent = raw["filings"]["recent"]
                content = {
                    "cik": cik,
                    "name": raw["name"],
                    "sic_description": raw.get("sicDescription"),
                    "filings": _recent_filings(recent, started_at)[:40],
                }
            identifier = "sec-" + hashlib.sha256(dumps(content).encode()).hexdigest()[:20]
            sources.append(
                {
                    "id": identifier,
                    "url": url,
                    "source": "SEC EDGAR",
                    "retrieved_at": now(),
                    "content": content,
                }
            )
        news = self.data.news(symbol, (started_at - timedelta(days=7)).isoformat())
        for article in news:
            if not started_at - timedelta(days=7) <= news_time(article) <= started_at:
                continue
            sources.append(
                {
                    "id": "news-"
                    + str(article["id"])
                    + "-"
                    + hashlib.sha256(dumps(article).encode()).hexdigest()[:12],
                    "source": article.get("source", "Alpaca news"),
                    "url": article.get("url"),
                    "published_at": article.get("created_at"),
                    "updated_at": article.get("updated_at", article["created_at"]),
                    "retrieved_at": now(),
                    "content": {k: article.get(k) for k in ("headline", "summary", "content")},
                }
            )
        bundle = {
            "sec_schema": 7,
            "symbol": symbol,
            "fetched_at": started_at.isoformat(),
            "sources": sources,
            "news_coverage": "Paginated vendor news within the requested window; absence is not proof of no material news",
        }
        return bundle

    @traced("evidence.filings_since")
    def filings_since(self, symbol, since):
        since = dt(since)
        cutoff = now()
        if not timedelta(0) <= cutoff - since < timedelta(days=365):
            raise ValueError("Filing freshness cutoff is outside recent submissions coverage")
        cik = self.cfg.research_universe[symbol].cik.zfill(10)
        raw = self.request(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
        if str(raw.get("cik", "")).zfill(10) != cik:
            raise ValueError("SEC response company identity does not match configured CIK")
        recent = raw["filings"]["recent"]
        filings = _recent_filings(recent, cutoff)
        if not recent["form"]:
            raise ValueError("SEC recent submissions are empty")
        return [filing for filing in filings if dt(filing["acceptanceDateTime"]) >= since]

    @traced("evidence.documents")
    def documents(self, symbol, filing):
        accession = filing["accessionNumber"]
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            raise ValueError("Invalid SEC accession")
        cik = int(self.cfg.research_universe[symbol].cik)
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"
        raw = self.request(base + "index.json").json()
        items = [
            item
            for item in raw["directory"]["item"]
            if re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:htm|html|txt)", item["name"])
        ]
        primary = filing.get("primaryDocument")
        if any(item["name"] == primary for item in items):
            # The submission bundle duplicates the primary filing and separately listed exhibits.
            items = [item for item in items if item["name"] != accession + ".txt" or item["name"] == primary]
            items.sort(key=lambda item: item["name"] != primary)
        return [
            {
                "url": base + quote(item["name"], safe=""),
                "name": item["name"],
                "published_at": filing.get("acceptanceDateTime", filing["filingDate"]),
                "form": filing["form"],
            }
            for item in items
        ][:80]

    @traced("evidence.document")
    def document(self, metadata):
        raw = self.request(metadata["url"]).text
        parser = FilingText()
        parser.feed(raw)
        return re.sub(r"\s+", " ", "".join(parser.parts)).strip()
