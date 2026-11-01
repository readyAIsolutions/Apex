"""APEX settings — env-driven, no secrets in code (ADR-0002)."""
import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # DB / queue
    database_url: str = "postgresql://apex_app:apex_app_dev_password@127.0.0.1:5432/apex"
    migrator_url: str = "postgresql://apex_migrator:apex_migrator_dev_password@127.0.0.1:5432/apex"
    redis_url: str = "redis://127.0.0.1:6379/4"
    job_queue: str = "apex:jobs"

    # bootstrap keys (seeded hashed into api_keys)
    bootstrap_admin_key: str = "apex_dev_admin_key_change_me"
    bootstrap_keys: str = ""

    # Odoo CRM (the replacement for HighLevel — ADR-0001)
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_user: str = ""
    odoo_password: str = ""
    odoo_mock: bool = True
    odoo_lead_model: str = "crm.lead"
    odoo_partner_model: str = "res.partner"
    odoo_external_field: str = "apex_external_ref"

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Brevo (email)
    brevo_api_key: str = ""
    brevo_sender_email: str = ""

    # Pilot tenant defaults
    default_tenant_name: str = "AIMBEX Technologies"
    default_timezone: str = "America/Edmonton"
    default_currency: str = "CAD"
    approval_ttl_days: int = 7
    log_level: str = "INFO"

    def webhook_secret(self, provider: str) -> str | None:
        return os.environ.get(f"WEBHOOK_SECRET_{provider}")

    def webhook_secrets(self) -> dict[str, str]:
        return {k.removeprefix("WEBHOOK_SECRET_"): v for k, v in os.environ.items() if k.startswith("WEBHOOK_SECRET_")}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
