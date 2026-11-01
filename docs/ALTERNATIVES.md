# APEX vendor map — chosen default + alternatives (+ how to swap)

The rule: every external tool is a **provider adapter** (`app/integ/<name>.py`) reached via
`provider_routes`. Swapping = new adapter + route row. Here is the full map for the
blueprint's vendor list, with Odoo replacing HighLevel.

| Blueprint vendor | Chosen (default) | Alternatives (drop-in) | Swap = new adapter |
|---|---|---|---|
| CRM: **HighLevel (EXCLUDED)** | **Odoo 17 Community** (self-host, owns data, crm.lead) | ERPNext, SuiteCRM, HubSpot, Twenty (open-source), Attio | odoo_crm.py → erpnext.py etc. |
| Email nurture: CRM messaging | **Brevo** (API key, webhooks) | Odoo Mailing (no extra vendor), Amazon SES, Resend, Postmark | brevo_email.py |
| Model gateway | **OpenRouter** (per-task routes) | Direct Anthropic/OpenAI/DeepSeek keys; local Ollama/vLLM | openrouter.py |
| Search console | **Google Search Console API** (OAuth) | Bing Webmaster Tools | gsc.py |
| Analytics | **GA4 Data API** | Plausible, PostHog, Umami (self-host) | ga4.py |
| Ads (reporting first) | **Google Ads API + Meta Graph** | Microsoft Ads | google_ads.py, meta_ads.py |
| Keyword/SERP research | **DataForSEO** (metered, intent) | Semrush, Ahrefs, Google Keyword Planner (manual) | dataforseo.py |
| Link research | Semrush (defer) | DataForSEO backlinks | semrush.py |
| Social scheduling | **Buffer** (simple API) | Metricool, Later, n8n native nodes | social_buffer.py |
| Video avatar | **HeyGen** (render jobs) | Synthesia, D-ID; open alt: SadTalker/RenderHorse | heygen.py |
| Video/B-roll gen | **Runway** | Luma, Pika, Veo via fal; open alt: MoneyPrinterTurbo, ffmpeg assembly | runway.py |
| Voice | **ElevenLabs** | OpenAI TTS, Azure; free self-host: Piper, Coqui, edge-tts | elevenlabs.py |
| Render/assembly | **FFmpeg** (typed facade) | Creatomate (template API), Remotion (deterministic) | ffmpeg_media.py |
| Workflow plumbing | **n8n (optional)** | Windmill, Temporal, plain worker (current default) | n8n_flow.py |
| Queue | **Redis** | RabbitMQ, pg (SKIP LOCKED) | config only |
| Object media | (deferred) | S3-compatible: MinIO (self-host), Backblaze B2, Wasabi | storage.py later |

## How a swap actually happens (worked example: CRM HighLevel→Odoo, done)

1. `app/integ/odoo_crm.py` implements the adapter interface (XML-RPC, mock mode).
2. Registered in `app/integ/__init__.py` → `REGISTRY["odoo"]`.
3. `provider_connections` row for odoo; `provider_routes` row: task crm.sync → odoo.
4. Pipeline calls `registry.route("crm.sync", payload)` → odoo adapter. Done.
   HighLevel was never wired in — the slot exists, the vendor was swapped at the
   registry level, which is the whole point.

## Swapping later (new software appears)

- Write `app/integ/newthing.py` (40–150 lines, same interface).
- `REGISTRY["newthing"] = NewThingAdapter`.
- Update the `provider_routes` row(s) for the task type (or env override).
- Run `scripts/verify.py` — adapters are health-checked before routing; unconfigured
  ones fail closed, they never break the pipeline.
