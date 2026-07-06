"""Configuration management — loaded from .env and environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "info"
    debug: bool = False  # DS_GW_DEBUG

    # Upstream DeepSeek
    deepseek_api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    beta_base_url: str = "https://api.deepseek.com/beta"
    request_timeout: int = 300
    max_retries: int = 2

    # 8 Claude model slots → real DeepSeek models (for Claude Desktop masquerade)
    model_slots: dict = {
        "claude-sonnet-4-20250514":    "deepseek-v4-pro[1m]",
        "claude-opus-4-20250514":      "deepseek-v4-pro[1m]",
        "claude-3-5-sonnet-20241022":  "deepseek-v4-pro[1m]",
        "claude-3-opus-20240229":      "deepseek-v4-pro[1m]",
        "claude-3-haiku-20240307":     "deepseek-v4-flash",
        "claude-3-5-haiku-20241022":   "deepseek-v4-flash",
        "claude-3-5-sonnet-20240620":  "deepseek-v4-pro[1m]",
        "claude-3-sonnet-20240229":    "deepseek-v4-pro[1m]",
    }

    # Responses API model mapping (OpenAI model → DeepSeek model)
    # NOTE: Do NOT use [1m] suffix here — DeepSeek Chat Completions API rejects it.
    # [1m] is a Claude Code client-side convention only.
    responses_model_map: dict = {
        "gpt-5.5":                    "deepseek-v4-pro",
        "gpt-5.4":                    "deepseek-v4-pro",
        "gpt-5.4-mini":               "deepseek-v4-pro",
        "gpt-5.3-codex":              "deepseek-v4-pro",
        "gpt-5.2":                    "deepseek-v4-pro",
    }

    model_config = {"env_prefix": "DS_GW_", "env_file": ".env", "env_nested_delimiter": "__"}

    @property
    def anthropic_endpoint(self) -> str:
        return f"{self.base_url}/anthropic"

    @property
    def chat_completions_endpoint(self) -> str:
        return f"{self.base_url}/v1/chat/completions"

    @property
    def beta_chat_completions_endpoint(self) -> str:
        return f"{self.beta_base_url}/v1/chat/completions"

    @property
    def is_configured(self) -> bool:
        return bool(self.deepseek_api_key and self.deepseek_api_key != "sk-your-deepseek-api-key-here")


# Singleton
_settings: Settings | None = None


def load_config() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
