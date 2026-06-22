"""Model gateway — one `complete(system, prompt)` interface, swappable backend.

Backends (env MODEL_BACKEND):
  stub      (default) — deterministic, no model, zero resources. Used by all tests + CI.
  llamacpp            — OpenAI-compatible HTTP to the llama.cpp server CONTAINER (CPU). This is the
                        local stand-in for an EC2 model node — runs in docker next to LocalStack.
                        Aliases: http / openai / mlx all route to the same OpenAI-shaped client.

The interface is OpenAI-shaped so any OpenAI-compatible server (llama.cpp container, a cloud LLM, or
an MLX host server) is a pure config flip — only MODEL_BASE_URL / MODEL_ID change.
"""
import os

import httpx

_HTTP_BACKENDS = {"llamacpp", "http", "openai", "mlx"}


class ModelGateway:
    def __init__(self, backend=None, base_url=None, model_id=None, timeout=120.0):
        self.backend = (backend or os.environ.get("MODEL_BACKEND", "stub")).lower()
        self.base_url = base_url or os.environ.get("MODEL_BASE_URL", "http://localhost:8081/v1")
        self.model_id = model_id or os.environ.get(
            "MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
        )
        self.timeout = timeout

    def complete(self, prompt: str, system: str = "You are a concise, helpful assistant.") -> str:
        if self.backend == "stub":
            return self._stub(system, prompt)
        if self.backend in _HTTP_BACKENDS:
            return self._openai_http(system, prompt)
        raise ValueError("unknown MODEL_BACKEND: {}".format(self.backend))

    # --- backends -------------------------------------------------------------
    def _stub(self, system: str, prompt: str) -> str:
        """Deterministic, model-free response so tests never need a model server."""
        snippet = " ".join(prompt.split())[:200]
        return "[stub-summary] {}".format(snippet)

    def _openai_http(self, system: str, prompt: str) -> str:
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 512,
        }
        resp = httpx.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    def health(self) -> bool:
        if self.backend == "stub":
            return True
        try:
            httpx.get(self.base_url.rstrip("/") + "/models", timeout=5.0).raise_for_status()
            return True
        except Exception:  # noqa: BLE001
            return False


# alias map note: MODEL_BACKEND=llamacpp targets the docker llama.cpp server (CPU, OpenAI API).


def complete(prompt: str, system: str = "You are a concise, helpful assistant.") -> str:
    """Module-level convenience using env-configured defaults."""
    return ModelGateway().complete(prompt, system)
