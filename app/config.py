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


settings = Settings()
