"""Integration registry + data-driven router (ADR-0002).

Add software: build an adapter, add one line here, point provider_routes at it.
Remove software: disable the route row / drop the adapter — pipeline untouched.
"""
import os
import uuid
from typing import Optional

from app.config import settings
from app.integ.base import LocalExecutorAdapter, ProviderAdapter, RouteUnavailable, ShellAdapter
from app.integ.brevo import BrevoEmailAdapter
from app.integ.dev_demand import DevDemandAdapter
from app.integ.ga4 import GA4Adapter
from app.integ.gsc import GSCAdapter
from app.integ.odoo_crm import OdooCRMAdapter
from app.integ.openrouter import OpenRouterAdapter


def build_registry() -> dict[str, ProviderAdapter]:
    return {
        # ---- real adapters (implemented + CI-verified) ----
        "odoo": OdooCRMAdapter(
            url=settings.odoo_url, db=settings.odoo_db, user=settings.odoo_user,
            password=settings.odoo_password, mock=settings.odoo_mock,
            lead_model=settings.odoo_lead_model, partner_model=settings.odoo_partner_model,
            external_field=settings.odoo_external_field),
        "openrouter": OpenRouterAdapter(
            api_key=settings.openrouter_api_key, base_url=settings.openrouter_base_url),
        "brevo": BrevoEmailAdapter(api_key=settings.brevo_api_key,
                                   sender_email=settings.brevo_sender_email),
        "local": LocalExecutorAdapter(),

        # ---- demand intelligence: REAL google adapters (fails closed without creds) ----
        "gsc": GSCAdapter(),          # real Search Console API (service-account OAuth)
        "ga4": GA4Adapter(),          # real GA4 Data API
        "dev_demand": DevDemandAdapter(),  # deterministic simulated source for keyless E2E

        # ---- honest shells: slots that fail closed until wired (see ALTERNATIVES.md) ----
        "google_ads": ShellAdapter("google_ads", ["GOOGLE_OAUTH_JSON", "GOOGLE_ADS_CUSTOMER_ID"],
                                   "POST https://googleads.googleapis.com/v17/customers/{id}/googleAds:searchStream"),
        "meta_ads": ShellAdapter("meta_ads", ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"],
                                 "GET https://graph.facebook.com/v21.0/act_{id}/insights"),
        "dataforseo": ShellAdapter("dataforseo", ["DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"],
                                   "POST https://api.dataforseo.com/v3/dataforseo_labs/google/search_intent/live"),
        "semrush": ShellAdapter("semrush", ["SEMRUSH_API_KEY"],
                                "GET https://oauth.semrush.com/api/v1/?type=backlinks_overview"),
        "buffer": ShellAdapter("buffer", ["BUFFER_ACCESS_TOKEN"],
                               "POST https://api.bufferapp.com/v1/updates/create.json"),
        "metricool": ShellAdapter("metricool", ["METRICOOL_API_KEY"],
                                  "POST https://rest.metricool.com/v3/posts"),
        "heygen": ShellAdapter("heygen", ["HEYGEN_API_KEY"],
                               "POST https://api.heygen.com/v2/video/generate"),
        "runway": ShellAdapter("runway", ["RUNWAY_API_KEY"],
                               "POST https://api.dev.runwayml.com/v1/text_to_video"),
        "elevenlabs": ShellAdapter("elevenlabs", ["ELEVENLABS_API_KEY"],
                                   "POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"),
        "creatomate": ShellAdapter("creatomate", ["CREATOMATE_API_KEY"],
                                   "POST https://api.creatomate.com/v1/renders"),
        "ffmpeg": ShellAdapter("ffmpeg", [], "local CLI (typed facade)", binary="ffmpeg"),
    }


REGISTRY: dict[str, ProviderAdapter] = build_registry()


class Router:
    """task_type -> adapter. Env first (APEX_ROUTE_<TASK>), then provider_routes rows,
    fails CLOSED with RouteUnavailable when nothing is routed."""

    def __init__(self, adapters: Optional[dict[str, ProviderAdapter]] = None):
        self.adapters = adapters or REGISTRY

    async def route(self, task_type: str, conn=None, tenant_id: Optional[uuid.UUID] = None) -> ProviderAdapter:
        env_provider = os.environ.get(f"APEX_ROUTE_{task_type.upper().replace('.', '_')}")
        if env_provider and env_provider in self.adapters:
            return self.adapters[env_provider]
        if conn is not None and tenant_id is not None:
            row = await conn.fetchrow(
                "SELECT provider FROM provider_routes WHERE tenant_id = $1 AND task_type = $2 "
                "AND enabled ORDER BY id LIMIT 1", tenant_id, task_type)
            if row and row["provider"] in self.adapters:
                return self.adapters[row["provider"]]
        raise RouteUnavailable(f"no provider routed for task_type='{task_type}' (add a provider_routes row or APEX_ROUTE_{task_type.upper()})")


router = Router()
