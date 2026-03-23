"""Search templates — predefined search profiles for common use cases."""

TEMPLATES = {
    "recruiter": {
        "name": "Recruiter Check",
        "description": "Verify candidate identity and professional presence",
        "scanners": ["social", "web", "professional", "email"],
        "focus": ["linkedin", "xing", "github", "stackoverflow"],
        "priority": "professional",
    },
    "due_diligence": {
        "name": "Due Diligence",
        "description": "Comprehensive background check for business partners",
        "scanners": ["social", "web", "email", "professional", "public_records", "data_enrichment"],
        "focus": ["all"],
        "priority": "comprehensive",
    },
    "journalist": {
        "name": "Journalist Research",
        "description": "Verify source credibility and find public information",
        "scanners": ["social", "web", "email", "data_enrichment"],
        "focus": ["twitter", "linkedin", "news"],
        "priority": "web",
    },
    "osint_basic": {
        "name": "Basic OSINT",
        "description": "Quick social media and web presence check",
        "scanners": ["social", "web"],
        "focus": ["all_social"],
        "priority": "speed",
    },
    "osint_full": {
        "name": "Full OSINT",
        "description": "Maximum coverage across all available scanners",
        "scanners": ["social", "web", "email", "professional", "image", "advanced_image", "reverse_image", "deep_social", "public_records", "data_enrichment"],
        "focus": ["all"],
        "priority": "coverage",
    },
    "email_only": {
        "name": "Email Lookup",
        "description": "Find email addresses and check breach databases",
        "scanners": ["email", "data_enrichment"],
        "focus": ["email"],
        "priority": "email",
    },
    "image_search": {
        "name": "Image Search",
        "description": "Find images and reverse image search results",
        "scanners": ["image", "advanced_image", "reverse_image"],
        "focus": ["images"],
        "priority": "images",
    },
}


def get_templates() -> list[dict]:
    """Get all available templates."""
    return [
        {"id": k, **v}
        for k, v in TEMPLATES.items()
    ]


def get_template(template_id: str) -> dict | None:
    """Get a specific template."""
    return TEMPLATES.get(template_id)


def get_scanners_for_template(template_id: str) -> list[str]:
    """Get scanner list for a template."""
    template = TEMPLATES.get(template_id)
    if not template:
        return []
    return template["scanners"]
