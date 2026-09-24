"""Tests for location intelligence module."""

from app.analysis.location import (
    ALL_LOCATIONS,
    expand_location,
)


class TestExpandLocation:
    def test_german_city(self):
        result = expand_location("Oberhausen")
        assert result.city == "Oberhausen"
        assert result.state == "Nordrhein-Westfalen"
        assert result.state_abbr == "NRW"
        assert result.region == "Ruhrgebiet"
        assert result.country == "Germany"
        assert result.country_code == "DE"

    def test_case_insensitive(self):
        result = expand_location("oberhausen")
        assert result.city == "oberhausen"
        assert result.country == "Germany"

    def test_austrian_city(self):
        result = expand_location("Wien")
        assert result.country == "Austria"
        assert result.country_code == "AT"
        assert "Wien" in result.search_terms
        assert "Austria" in result.search_terms

    def test_swiss_city(self):
        result = expand_location("Zürich")
        assert result.country == "Switzerland"
        assert result.country_code == "CH"

    def test_us_city(self):
        result = expand_location("Manhattan")
        assert result.state == "New York"
        assert result.country == "USA"
        assert result.country_code == "US"

    def test_uk_city(self):
        result = expand_location("London")
        assert result.country == "United Kingdom"
        assert result.country_code == "GB"

    def test_unknown_location(self):
        result = expand_location("Atlantis")
        assert result.city is None
        assert result.country is None
        assert result.search_terms == ["Atlantis"]

    def test_whitespace(self):
        result = expand_location("  Berlin  ")
        assert result.country == "Germany"

    def test_search_terms_order(self):
        result = expand_location("Oberhausen")
        # Should go from specific to general
        assert result.search_terms[0] == "Oberhausen"
        assert result.search_terms[-1] == "Germany"
        assert "NRW" in result.search_terms
        assert "Ruhrgebiet" in result.search_terms

    def test_no_duplicate_terms(self):
        result = expand_location("Berlin")
        assert len(result.search_terms) == len(set(result.search_terms))


class TestLocationCoverage:
    def test_german_coverage(self):
        # Major German cities should be in the database
        for city in ["berlin", "münchen", "hamburg", "köln", "frankfurt", "stuttgart"]:
            assert city in ALL_LOCATIONS, f"{city} missing from locations"

    def test_austrian_coverage(self):
        for city in ["wien", "graz", "salzburg"]:
            assert city in ALL_LOCATIONS, f"{city} missing"

    def test_all_locations_have_country(self):
        for city, info in ALL_LOCATIONS.items():
            assert "country" in info, f"{city} missing country"
            assert "country_code" in info, f"{city} missing country_code"
