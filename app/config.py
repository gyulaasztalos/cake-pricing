"""App configuration from environment (12-factor)."""

from __future__ import annotations

import os


class Settings:
    app_env: str = os.getenv("APP_ENV", "prod")
    # UI language today; architecture is i18n-ready for more later (§5).
    default_locale: str = os.getenv("APP_LOCALE", "hu")
    # Customer auto-anonymization window in years (§3.7).
    anonymize_after_years: int = int(os.getenv("ANONYMIZE_AFTER_YEARS", "5"))
    # Amount stepper default step for gram/millilitre units (§ UI, unit-aware step).
    mass_volume_step: int = int(os.getenv("MASS_VOLUME_STEP", "10"))
    # Bearer token for the customer-intake API (§8a). Empty = intake disabled.
    intake_token: str = os.getenv("INTAKE_TOKEN", "")
    # Secret path segment for the published .ics calendar feed. Calendar apps
    # cannot do Authentik forward-auth, so the feed URL carries an unguessable
    # token instead ("machines use tokens, humans use Authentik" — PLANNING
    # §Calendar). The feed exposes customer names and prices, so an empty
    # token disables it entirely (404).
    calendar_token: str = os.getenv("CALENDAR_TOKEN", "")


settings = Settings()
