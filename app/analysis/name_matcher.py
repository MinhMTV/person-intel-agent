"""Name matching and filtering for search results.

Filters results to only include those that match the target person's name.
"""

import re
from typing import Optional


class NameMatcher:
    """Match search results against a target person's name."""
    
    def __init__(self, full_name: str, include_middle: bool = True):
        self.full_name = full_name.strip()
        self.include_middle = include_middle
        self.parts = self.full_name.split()
        self.first_name = self.parts[0].lower() if self.parts else ""
        self.last_name = self.parts[-1].lower() if len(self.parts) > 1 else ""
        self.middle_name = self.parts[1].lower() if len(self.parts) > 2 else None
        
        # Generate valid name variants
        self.valid_variants = self._generate_variants()
    
    def _generate_variants(self) -> set[str]:
        """Generate all valid name variants to match against."""
        variants = set()
        
        # Full name variants
        variants.add(self.full_name.lower())
        variants.add(f"{self.first_name} {self.last_name}")
        if self.middle_name and self.include_middle:
            variants.add(f"{self.first_name} {self.middle_name} {self.last_name}")
            variants.add(f"{self.first_name} {self.middle_name[0]}. {self.last_name}")
        
        # Username-style variants
        variants.add(f"{self.first_name}{self.last_name}")
        variants.add(f"{self.first_name}.{self.last_name}")
        variants.add(f"{self.first_name}_{self.last_name}")
        if self.middle_name:
            variants.add(f"{self.first_name[0]}{self.middle_name}{self.last_name}")
            variants.add(f"{self.first_name}{self.middle_name[0]}{self.last_name}")
        
        # Last, First variants
        variants.add(f"{self.last_name}, {self.first_name}")
        if self.middle_name:
            variants.add(f"{self.last_name}, {self.first_name} {self.middle_name}")
        
        return variants
    
    def matches(self, text: str) -> bool:
        """Check if text matches the target person's name."""
        if not text:
            return False
        
        text_lower = text.lower()
        
        # Check exact variants
        for variant in self.valid_variants:
            if variant in text_lower:
                return True
        
        # Check if all key name parts appear (order-independent but strict)
        # Must have first AND last name
        if self.first_name and self.last_name:
            has_first = self.first_name in text_lower
            has_last = self.last_name in text_lower
            
            if has_first and has_last:
                # Check that it's not a DIFFERENT person with same first/last
                # (e.g., "Ngoc Minh Vuong" has "minh" and "vuong" but is different)
                # Heuristic: if middle name exists in text but doesn't match ours, reject
                if self.middle_name:
                    # Check if text contains a different middle name
                    words = text_lower.split()
                    if self.middle_name in words:
                        return True  # Has our middle name
                    # Check if there's a different middle name between first and last
                    first_idx = -1
                    last_idx = -1
                    for i, w in enumerate(words):
                        if w == self.first_name:
                            first_idx = i
                        if w == self.last_name:
                            last_idx = i
                    if first_idx >= 0 and last_idx >= 0 and last_idx > first_idx + 1:
                        # There's a word between first and last that's not our middle name
                        middle_word = words[first_idx + 1]
                        if middle_word != self.middle_name and len(middle_word) > 1:
                            return False  # Different person
                    return has_first and has_last
                else:
                    return has_first and has_last
        
        return False
    
    def filter_results(self, results: list, title_field: str = "title", 
                       url_field: str = "url", snippet_field: str = "snippet") -> list:
        """Filter results to only include matching names."""
        filtered = []
        for r in results:
            # Get text to check
            text_parts = []
            if hasattr(r, title_field):
                text_parts.append(getattr(r, title_field, ""))
            if hasattr(r, url_field):
                text_parts.append(getattr(r, url_field, ""))
            if hasattr(r, snippet_field):
                text_parts.append(getattr(r, snippet_field, ""))
            
            # Also check dict-style results
            if isinstance(r, dict):
                text_parts.append(r.get(title_field, ""))
                text_parts.append(r.get(url_field, ""))
                text_parts.append(r.get(snippet_field, ""))
            
            combined_text = " ".join(str(p) for p in text_parts if p)
            
            if self.matches(combined_text):
                filtered.append(r)
        
        return filtered
    
    def is_strict_match(self, text: str) -> bool:
        """Strict match — exact name must appear."""
        if not text:
            return False
        text_lower = text.lower()
        return self.full_name.lower() in text_lower or \
               f"{self.first_name} {self.last_name}" in text_lower
