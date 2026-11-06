from __future__ import annotations

import hashlib
import re
import threading
import time
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import httpx

from .models import dec, dt, dumps, now
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
        if symbol not in self.cfg.universe:
            raise ValueError("Symbol outside configured research universe")
        cached = self.store.get(symbol, "evidence_cache")
        if (
            cached
            and not fresh
            and now() - dt(cached["fetched_at"]) < timedelta(hours=self.cfg.research_cache_hours)
        ):
            return cached
        bundle = self.fetch(symbol)
        self.store.put(symbol, bundle, "evidence_cache")
        return bundle

    def fetch(self, symbol):
        if symbol not in self.cfg.universe:
            raise ValueError("Symbol outside configured research universe")
        if not self.cfg.sec_user_agent or "@" not in self.cfg.sec_user_agent:
            raise ValueError("Set SEC user-agent application name and contact email")
        cik = self.cfg.universe[symbol].cik.zfill(10)
        sources = []
        tags = {
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "NetIncomeLoss",
            "NetCashProvidedByUsedInOperatingActivities",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "CashAndCashEquivalentsAtCarryingValue",
            "LongTermDebtCurrent",
            "LongTermDebtNoncurrent",
            "StockBasedCompensation",
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
            if kind == "facts":
                values = {}
                for taxonomy in raw["facts"].values():
                    for tag, entry in taxonomy.items():
                        if tag in tags:
                            values[tag] = {
                                unit: sorted(
                                    [v for v in records if v.get("filed", "") <= now().date().isoformat()],
                                    key=lambda x: (x.get("filed", ""), x.get("end", "")),
                                    reverse=True,
                                )[:12]
                                for unit, records in entry["units"].items()
                            }
                if not values:
                    raise ValueError("SEC evidence lacks usable financial facts")
                content = {
                    "entity": raw["entityName"],
                    "facts": values,
                    "caution": "Facts may repeat/restated; compare matching fiscal durations and units; do not sum overlapping YTD periods.",
                }
            else:
                recent = raw["filings"]["recent"]
                content = {
                    "name": raw["name"],
                    "sic_description": raw.get("sicDescription"),
                    "filings": [
                        {k: v[i] for k, v in recent.items()}
                        for i, form in enumerate(recent["form"])
                        if form.removesuffix("/A") in {"10-K", "10-Q", "8-K", "20-F", "6-K"}
                        and recent["filingDate"][i] <= now().date().isoformat()
                    ][:40],
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
        news = self.data.news(symbol, (now() - timedelta(days=7)).isoformat())
        for article in news:
            sources.append(
                {
                    "id": "news-"
                    + str(article["id"])
                    + "-"
                    + hashlib.sha256(dumps(article).encode()).hexdigest()[:12],
                    "source": article.get("source", "Alpaca news"),
                    "url": article.get("url"),
                    "published_at": article.get("created_at"),
                    "retrieved_at": now(),
                    "content": {k: article.get(k) for k in ("headline", "summary", "content")},
                }
            )
        bundle = {
            "symbol": symbol,
            "fetched_at": now().isoformat(),
            "sources": sources,
            "news_coverage": "Up to 50 recent vendor articles; absence is not proof of no material news",
        }
        return bundle

    @traced("evidence.documents")
    def documents(self, symbol, filing):
        accession = filing["accessionNumber"]
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            raise ValueError("Invalid SEC accession")
        cik = int(self.cfg.universe[symbol].cik)
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
