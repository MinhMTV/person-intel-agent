"""Location Intelligence — geographic expansion for broader search coverage.

Takes a location string and expands it hierarchically:
  "Oberhausen" → "NRW" → "Ruhrgebiet" → "Germany"
  "Manhattan" → "New York" → "NYC" → "USA"

Provides:
  - Location parsing (extract city, state, country)
  - Geo-expansion (build broader search terms)
  - Location-boosted search queries
"""

from __future__ import annotations
from dataclasses import dataclass, field

# German cities → state → region → country
GERMAN_LOCATIONS: dict[str, dict] = {
    # NRW cities
    "oberhausen": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Ruhrgebiet", "country": "Germany", "country_code": "DE"},
    "essen": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Ruhrgebiet", "country": "Germany", "country_code": "DE"},
    "duisburg": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Ruhrgebiet", "country": "Germany", "country_code": "DE"},
    "dortmund": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Ruhrgebiet", "country": "Germany", "country_code": "DE"},
    "bochum": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Ruhrgebiet", "country": "Germany", "country_code": "DE"},
    "gelsenkirchen": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Ruhrgebiet", "country": "Germany", "country_code": "DE"},
    "duesseldorf": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Rheinland", "country": "Germany", "country_code": "DE"},
    "düsseldorf": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Rheinland", "country": "Germany", "country_code": "DE"},
    "koeln": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Rheinland", "country": "Germany", "country_code": "DE"},
    "köln": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Rheinland", "country": "Germany", "country_code": "DE"},
    "bonn": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Rheinland", "country": "Germany", "country_code": "DE"},
    "aachen": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "", "country": "Germany", "country_code": "DE"},
    "bielefeld": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Ostwestfalen-Lippe", "country": "Germany", "country_code": "DE"},
    "muenster": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Münsterland", "country": "Germany", "country_code": "DE"},
    "münster": {"state": "Nordrhein-Westfalen", "state_abbr": "NRW", "region": "Münsterland", "country": "Germany", "country_code": "DE"},
    # Bavaria
    "muenchen": {"state": "Bayern", "state_abbr": "BY", "region": "Oberbayern", "country": "Germany", "country_code": "DE"},
    "münchen": {"state": "Bayern", "state_abbr": "BY", "region": "Oberbayern", "country": "Germany", "country_code": "DE"},
    "nuernberg": {"state": "Bayern", "state_abbr": "BY", "region": "Mittelfranken", "country": "Germany", "country_code": "DE"},
    "nürnberg": {"state": "Bayern", "state_abbr": "BY", "region": "Mittelfranken", "country": "Germany", "country_code": "DE"},
    "augsburg": {"state": "Bayern", "state_abbr": "BY", "region": "Schwaben", "country": "Germany", "country_code": "DE"},
    # Berlin/Brandenburg
    "berlin": {"state": "Berlin", "state_abbr": "BE", "region": "Berlin-Brandenburg", "country": "Germany", "country_code": "DE"},
    "potsdam": {"state": "Brandenburg", "state_abbr": "BB", "region": "Berlin-Brandenburg", "country": "Germany", "country_code": "DE"},
    # Hamburg/Schleswig-Holstein
    "hamburg": {"state": "Hamburg", "state_abbr": "HH", "region": "Norddeutschland", "country": "Germany", "country_code": "DE"},
    "kiel": {"state": "Schleswig-Holstein", "state_abbr": "SH", "region": "Norddeutschland", "country": "Germany", "country_code": "DE"},
    # Baden-Württemberg
    "stuttgart": {"state": "Baden-Württemberg", "state_abbr": "BW", "region": "Schwaben", "country": "Germany", "country_code": "DE"},
    "karlsruhe": {"state": "Baden-Württemberg", "state_abbr": "BW", "region": "", "country": "Germany", "country_code": "DE"},
    "mannheim": {"state": "Baden-Württemberg", "state_abbr": "BW", "region": "Rhein-Neckar", "country": "Germany", "country_code": "DE"},
    "freiburg": {"state": "Baden-Württemberg", "state_abbr": "BW", "region": "Südbaden", "country": "Germany", "country_code": "DE"},
    # Hessen
    "frankfurt": {"state": "Hessen", "state_abbr": "HE", "region": "Rhein-Main", "country": "Germany", "country_code": "DE"},
    "wiesbaden": {"state": "Hessen", "state_abbr": "HE", "region": "Rhein-Main", "country": "Germany", "country_code": "DE"},
    # Other
    "hannover": {"state": "Niedersachsen", "state_abbr": "NI", "region": "", "country": "Germany", "country_code": "DE"},
    "bremen": {"state": "Bremen", "state_abbr": "HB", "region": "Norddeutschland", "country": "Germany", "country_code": "DE"},
    "dresden": {"state": "Sachsen", "state_abbr": "SN", "region": "Ostdeutschland", "country": "Germany", "country_code": "DE"},
    "leipzig": {"state": "Sachsen", "state_abbr": "SN", "region": "Ostdeutschland", "country": "Germany", "country_code": "DE"},
    "erfurt": {"state": "Thüringen", "state_abbr": "TH", "region": "Ostdeutschland", "country": "Germany", "country_code": "DE"},
    "magdeburg": {"state": "Sachsen-Anhalt", "state_abbr": "ST", "region": "Ostdeutschland", "country": "Germany", "country_code": "DE"},
    "mainz": {"state": "Rheinland-Pfalz", "state_abbr": "RP", "region": "", "country": "Germany", "country_code": "DE"},
    "saarbruecken": {"state": "Saarland", "state_abbr": "SL", "region": "", "country": "Germany", "country_code": "DE"},
    "saarbrücken": {"state": "Saarland", "state_abbr": "SL", "region": "", "country": "Germany", "country_code": "DE"},
    "rostock": {"state": "Mecklenburg-Vorpommern", "state_abbr": "MV", "region": "Norddeutschland", "country": "Germany", "country_code": "DE"},
    "schwerin": {"state": "Mecklenburg-Vorpommern", "state_abbr": "MV", "region": "Norddeutschland", "country": "Germany", "country_code": "DE"},
}

# Austrian cities
AUSTRIAN_LOCATIONS: dict[str, dict] = {
    "wien": {"state": "Wien", "state_abbr": "W", "region": "Ostösterreich", "country": "Austria", "country_code": "AT"},
    "vienna": {"state": "Wien", "state_abbr": "W", "region": "Ostösterreich", "country": "Austria", "country_code": "AT"},
    "graz": {"state": "Steiermark", "state_abbr": "ST", "region": "", "country": "Austria", "country_code": "AT"},
    "linz": {"state": "Oberösterreich", "state_abbr": "OÖ", "region": "", "country": "Austria", "country_code": "AT"},
    "salzburg": {"state": "Salzburg", "state_abbr": "S", "region": "", "country": "Austria", "country_code": "AT"},
    "innsbruck": {"state": "Tirol", "state_abbr": "T", "region": "", "country": "Austria", "country_code": "AT"},
    "klagenfurt": {"state": "Kärnten", "state_abbr": "K", "region": "", "country": "Austria", "country_code": "AT"},
}

# Swiss cities
SWISS_LOCATIONS: dict[str, dict] = {
    "zurich": {"state": "Zürich", "state_abbr": "ZH", "region": "Deutschschweiz", "country": "Switzerland", "country_code": "CH"},
    "zürich": {"state": "Zürich", "state_abbr": "ZH", "region": "Deutschschweiz", "country": "Switzerland", "country_code": "CH"},
    "bern": {"state": "Bern", "state_abbr": "BE", "region": "Deutschschweiz", "country": "Switzerland", "country_code": "CH"},
    "basel": {"state": "Basel-Stadt", "state_abbr": "BS", "region": "Nordwestschweiz", "country": "Switzerland", "country_code": "CH"},
    "geneva": {"state": "Genève", "state_abbr": "GE", "region": "Romandie", "country": "Switzerland", "country_code": "CH"},
    "lausanne": {"state": "Vaud", "state_abbr": "VD", "region": "Romandie", "country": "Switzerland", "country_code": "CH"},
}

ALL_LOCATIONS = {**GERMAN_LOCATIONS, **AUSTRIAN_LOCATIONS, **SWISS_LOCATIONS}

# US cities
US_LOCATIONS: dict[str, dict] = {
    "new york": {"state": "New York", "state_abbr": "NY", "region": "East Coast", "country": "USA", "country_code": "US"},
    "manhattan": {"state": "New York", "state_abbr": "NY", "region": "New York City", "country": "USA", "country_code": "US"},
    "brooklyn": {"state": "New York", "state_abbr": "NY", "region": "New York City", "country": "USA", "country_code": "US"},
    "los angeles": {"state": "California", "state_abbr": "CA", "region": "West Coast", "country": "USA", "country_code": "US"},
    "san francisco": {"state": "California", "state_abbr": "CA", "region": "West Coast", "country": "USA", "country_code": "US"},
    "chicago": {"state": "Illinois", "state_abbr": "IL", "region": "Midwest", "country": "USA", "country_code": "US"},
    "boston": {"state": "Massachusetts", "state_abbr": "MA", "region": "East Coast", "country": "USA", "country_code": "US"},
    "seattle": {"state": "Washington", "state_abbr": "WA", "region": "West Coast", "country": "USA", "country_code": "US"},
    "austin": {"state": "Texas", "state_abbr": "TX", "region": "South", "country": "USA", "country_code": "US"},
    "miami": {"state": "Florida", "state_abbr": "FL", "region": "South", "country": "USA", "country_code": "US"},
}

ALL_LOCATIONS.update(US_LOCATIONS)

# UK cities
UK_LOCATIONS: dict[str, dict] = {
    "london": {"state": "England", "state_abbr": "ENG", "region": "Greater London", "country": "United Kingdom", "country_code": "GB"},
    "manchester": {"state": "England", "state_abbr": "ENG", "region": "North West", "country": "United Kingdom", "country_code": "GB"},
    "birmingham": {"state": "England", "state_abbr": "ENG", "region": "West Midlands", "country": "United Kingdom", "country_code": "GB"},
    "edinburgh": {"state": "Scotland", "state_abbr": "SCT", "region": "Lothian", "country": "United Kingdom", "country_code": "GB"},
    "glasgow": {"state": "Scotland", "state_abbr": "SCT", "region": "Strathclyde", "country": "United Kingdom", "country_code": "GB"},
}

ALL_LOCATIONS.update(UK_LOCATIONS)


@dataclass
class LocationExpansion:
    """Result of location expansion."""
    original: str
    city: str | None = None
    state: str | None = None
    state_abbr: str | None = None
    region: str | None = None
    country: str | None = None
    country_code: str | None = None
    search_terms: list[str] = field(default_factory=list)


def expand_location(location: str) -> LocationExpansion:
    """Expand a location string into hierarchical components.

    Example:
        expand_location("Oberhausen")
        → LocationExpansion(
            original="Oberhausen",
            city="Oberhausen",
            state="Nordrhein-Westfalen",
            state_abbr="NRW",
            region="Ruhrgebiet",
            country="Germany",
            country_code="DE",
            search_terms=["Oberhausen", "NRW", "Nordrhein-Westfalen", "Ruhrgebiet", "Germany"]
        )
    """
    key = location.lower().strip()
    result = LocationExpansion(original=location)

    # Check if we know this location
    info = ALL_LOCATIONS.get(key)

    if info:
        result.city = location.strip()
        result.state = info.get("state")
        result.state_abbr = info.get("state_abbr")
        result.region = info.get("region")
        result.country = info.get("country")
        result.country_code = info.get("country_code")

        # Build search terms: city → state_abbr → state → region → country
        terms = [location.strip()]
        if result.state_abbr:
            terms.append(result.state_abbr)
        if result.state:
            terms.append(result.state)
        if result.region:
            terms.append(result.region)
        if result.country:
            terms.append(result.country)
        result.search_terms = list(dict.fromkeys(terms))  # dedupe preserving order
    else:
        # Unknown location — just use as-is
        result.search_terms = [location.strip()]

    return result


def build_location_queries(name: str, locations: list[str]) -> list[str]:
    """Build search queries combining a name with expanded locations.

    Example:
        build_location_queries("John Smith", ["Oberhausen", "Berlin"])
        → ["John Smith Oberhausen", "John Smith NRW", "John Smith Ruhrgebiet",
           "John Smith Germany", "John Smith Berlin", ...]
    """
    queries = []
    for loc in locations:
        expansion = expand_location(loc)
        for term in expansion.search_terms:
            queries.append(f"{name} {term}")
    return queries


def get_location_hierarchy(location: str) -> list[str]:
    """Get the full hierarchy for a location (city → state → region → country).

    Returns list of terms from most specific to most general.
    """
    expansion = expand_location(location)
    return expansion.search_terms
