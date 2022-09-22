from datetime import datetime

import pytest


@pytest.mark.django_db
def test_env(settings, anon_client, site_profile_factory, extra_site):
    year = datetime.utcnow().year
    settings.DEVHUB_USERNAME = "abc"
    settings.TOKEN_LIFETIME_MINUTES = 5
    settings.PREFLIGHT_LIFETIME_MINUTES = 15
    settings.SENTRY_DSN = "https://example.com"
    site_profile_factory(welcome_text="Default")
    site_profile_factory(welcome_text="Extra", site=extra_site)

    response = anon_client.get("/")
    assert response.context["GLOBALS"] == {
        "PREFLIGHT_LIFETIME_MINUTES": 15,
        "TOKEN_LIFETIME_MINUTES": 5,
        "SITE": {
            "name": "MetaDeploy",
            "company_name": "Mao-Kwikowski Mercantile",
            "welcome_text": "<p>Default</p>",
            "master_agreement": "<p>MSA</p>",
            "copyright_notice": "<p>(c) 2022</p>",
            "show_product_tags": True,
            "show_metadeploy_wordmark": True,
            "company_logo": None,
            "favicon": None,
        },
        "YEAR": year,
        "SENTRY_DSN": "https://example.com",
        "SCRATCH_ORGS_AVAILABLE": True,
    }

    response = anon_client.get("/", SERVER_NAME=extra_site.domain)
    assert response.context["GLOBALS"] == {
        "PREFLIGHT_LIFETIME_MINUTES": 15,
        "TOKEN_LIFETIME_MINUTES": 5,
        "SITE": {
            "name": "MetaDeploy",
            "company_name": "Mao-Kwikowski Mercantile",
            "welcome_text": "<p>Extra</p>",
            "master_agreement": "<p>MSA</p>",
            "copyright_notice": "<p>(c) 2022</p>",
            "show_product_tags": True,
            "show_metadeploy_wordmark": True,
            "company_logo": None,
            "favicon": None,
        },
        "YEAR": year,
        "SENTRY_DSN": "https://example.com",
        "SCRATCH_ORGS_AVAILABLE": True,
    }
