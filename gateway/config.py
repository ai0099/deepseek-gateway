"""Configuration management — loaded from .env and environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "info"

    # Upstream DeepSeek
    deepseek_api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    request_timeout: int = 300
    max_retries: int = 2

    # 8 Claude model slots → real DeepSeek models (for Claude Desktop masquerade)
    model_slots: dict = {
        "claude-sonnet-4-20250514":    "deepseek-chat",
        "claude-opus-4-20250514":      "deepseek-reasoner",
        "claude-3-5-sonnet-20241022":  "deepseek-chat",
        "claude-3-opus-20240229":      "deepseek-reasoner",
        "claude-3-haiku-20240307":     "deepseek-chat",
        "claude-3-5-haiku-20241022":   "deepseek-chat",
        "claude-3-5-sonnet-20240620":  "deepseek-chat",
        "claude-3-sonnet-20240229":    "deepseek-chat",
    }

    # Responses API model mapping (OpenAI model → DeepSeek model)
    responses_model_map: dict = {
        "gpt-4o":                     "deepseek-chat",
        "gpt-4o-mini":                "deepseek-chat",
        "gpt-4.1":                    "deepseek-chat",
        "gpt-4.1-mini":               "deepseek-chat",
        "o1":                         "deepseek-reasoner",
        "o3":                         "deepseek-reasoner",
        "o3-mini":                    "deepseek-reasoner",
        "o4-mini":                    "deepseek-reasoner",
        "gpt-5":                      "deepseek-chat",
        "gpt-5.4":                    "deepseek-chat",
        "gpt-5.5":                    "deepseek-chat",
        "computer-use-preview":       "deepseek-chat",
        "gpt-4o-search-preview":      "deepseek-chat",
    }

    model_config = {"env_prefix": "DS_GW_", "env_nested_delimiter": "__"}

    @property
    def anthropic_endpoint(self) -> str:
        return f"{self.base_url}/anthropic"

    @property
    def chat_completions_endpoint(self) -> str:
        return f"{self.base_url}/v1/chat/completions"

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
