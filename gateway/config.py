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

    # Claude models → real DeepSeek models (for Claude Desktop masquerade)
    model_slots: dict = {
        "claude-fable-5[1m]":           "deepseek-v4-pro[1m]",
    }

    # Responses API model mapping (OpenAI model → DeepSeek model)
    # NOTE: [1m] suffix is a client-side convention indicating 1M context window.
    # resolve_responses() strips it from the resolved model before sending to
    # DeepSeek Chat Completions API (which does not support the suffix).
    responses_model_map: dict = {
        "gpt-5.5":                    "deepseek-chat",
        "gpt-5.6-sol":               "deepseek-v4-pro[1m]",
        "gpt-5.6-sol[1m]":           "deepseek-v4-pro[1m]",
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

# --- Constants ---
MAX_OUTPUT_TOKENS: int = 384000          # Anthropic: max output tokens (matches CLAUDE_CODE_MAX_OUTPUT_TOKENS=384000 + DeepSeek V4 max)
MAX_TOOL_RESULT_CHARS: int = 100000      # Anthropic: max chars per tool result
DEFAULT_MAX_OUTPUT_TOKENS: int = 393216  # Responses API: default max output tokens

# Singleton
_settings: Settings | None = None


def load_config() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
