"""Wikidata: public-figure records with a portrait (P18) for name hints."""

from __future__ import annotations

from urllib.parse import quote

import httpx

from app.config import Settings
from app.domain.identity import IdentityHints
from app.providers.base import check_response, json_or_raise
from app.providers.profiles.base import ProfileProvider, ProfileRecord

API = "https://www.wikidata.org/w/api.php"
HUMAN = "Q5"


def _claim_ids(claims: dict, prop: str) -> list[str]:
    out = []
    for claim in claims.get(prop, []) or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            out.append(value["id"])
        elif isinstance(value, str):
            out.append(value)
    return out


class WikidataProfileProvider(ProfileProvider):
    name = "wikidata"

    def __init__(self, settings: Settings, client: httpx.AsyncClient, max_entities: int = 3):
        self.settings = settings
        self.client = client
        self.max_entities = max_entities

    def is_configured(self) -> bool:
        return self.settings.wikidata_enabled

    def applicable(self, hints: IdentityHints) -> bool:
        return bool(hints.name)

    async def lookup(self, hints: IdentityHints) -> list[ProfileRecord]:
        if not hints.name:
            return []
        headers = {"User-Agent": self.settings.http_user_agent}
        resp = await self.client.get(
            API,
            params={
                "action": "wbsearchentities",
                "search": hints.name,
                "language": "en",
                "type": "item",
                "format": "json",
                "limit": self.max_entities,
            },
            headers=headers,
        )
        check_response(resp, self.name)
        data = json_or_raise(resp, self.name)
        ids = (
            [e["id"] for e in (data.get("search") or []) if isinstance(e, dict) and e.get("id")]
            if isinstance(data, dict)
            else []
        )
        if not ids:
            return []
        resp = await self.client.get(
            API,
            params={
                "action": "wbgetentities",
                "ids": "|".join(ids),
                "props": "labels|descriptions|claims|sitelinks",
                "languages": "en|de",
                "sitefilter": "enwiki|dewiki",
                "format": "json",
            },
            headers=headers,
        )
        check_response(resp, self.name)
        data = json_or_raise(resp, self.name)
        entities = data.get("entities", {}) if isinstance(data, dict) else {}
        records = []
        for entity_id in ids:
            entity = entities.get(entity_id) or {}
            claims = entity.get("claims") or {}
            if HUMAN not in _claim_ids(claims, "P31"):
                continue
            label = ((entity.get("labels") or {}).get("en") or (entity.get("labels") or {}).get("de") or {}).get(
                "value"
            )
            desc = ((entity.get("descriptions") or {}).get("en") or {}).get("value")
            images = _claim_ids(claims, "P18")
            sitelinks = entity.get("sitelinks") or {}
            wiki = sitelinks.get("enwiki") or sitelinks.get("dewiki")
            page_url = (
                f"https://{'en' if 'enwiki' in sitelinks else 'de'}.wikipedia.org/wiki/{quote(wiki['title'].replace(' ', '_'))}"
                if wiki
                else f"https://www.wikidata.org/wiki/{entity_id}"
            )
            records.append(
                ProfileRecord(
                    provider=self.name,
                    platform="wikipedia" if wiki else "wikidata",
                    url=page_url,
                    display_name=label,
                    bio=desc,
                    avatar_url=(
                        f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(images[0].replace(' ', '_'))}?width=600"
                        if images
                        else None
                    ),
                    linked_urls=[f"https://www.wikidata.org/wiki/{entity_id}"],
                    lookup=f"name:{hints.name}",
                )
            )
        return records
