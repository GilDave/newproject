"""Ollama: a local model on this computer, reached over http://127.0.0.1:11434 (no key, no cost, nothing leaves the machine).

Read-only roles only, like Antigravity: plan critic, build advisor, reviewer, analysis, video analysis and the like. It is
not a default participant: a small local model is weaker than the cloud AIs, so it joins only where it is picked.

Things that go wrong with local models, and what is done about them:
- Ollama silently cuts a prompt that does not fit its context window (num_ctx). A cut prompt gives a confident answer about
  half the evidence, so a prompt that fills the window is an ERROR here, never a result.
- "Thinking" models (e.g. qwen3) write long reasoning first. It is switched off (think=false) and any <think> block is removed.
- When the prompt asks for JSON, the request asks Ollama to produce valid JSON (format="json").
- Only localhost is allowed: prompts contain project code.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from ..tokens import estimate_tokens
from .base import CliProvider, ProviderError, ProviderResult, ProviderTimeout, ProviderUnavailable

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_NUM_CTX = 16384
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_THINK = re.compile(r"<think>.*?</think>", re.S)
_cache: dict[str, tuple[float, Optional[list[str]]]] = {}


def check_url(url: str) -> str:
    u = urllib.parse.urlparse(url)
    if u.scheme != "http" or (u.hostname or "").lower() not in LOCAL_HOSTS:
        raise ValueError("Ollama는 이 컴퓨터(127.0.0.1)에서만 연결합니다: 프롬프트에 프로젝트 코드가 들어 있어 다른 곳으로 보내지 않습니다.")
    return f"http://{u.netloc}"


def wants_json(prompt: str) -> bool:
    return "JSON object" in prompt and "ONLY" in prompt


class OllamaProvider(CliProvider):
    name = "ollama"
    cli_name = "ollama"
    supports_write = False
    default_participant = False  # not asked unless picked: it is a helper, not a replacement for the cloud AIs
    local = True

    def __init__(self, executable: Optional[str] = None, extra_args: Optional[list[str]] = None, *, url: str = DEFAULT_URL,
                 model: str = DEFAULT_MODEL, num_ctx: int = DEFAULT_NUM_CTX, keep_alive: str = "30m"):
        super().__init__(executable, extra_args)
        self.url = check_url(url)
        self.model = model
        self.num_ctx = int(num_ctx)
        self.keep_alive = keep_alive

    # ------------------------------------------------------------------ availability
    def _models(self, timeout: float = 1.5) -> Optional[list[str]]:
        """Installed model names, or None when the server cannot be reached (cached for 15 s)."""
        hit = _cache.get(self.url)
        if hit and time.time() - hit[0] < 15:
            return hit[1]
        names: Optional[list[str]]
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            names = [str(m.get("name", "")) for m in data.get("models", [])]
        except (urllib.error.URLError, OSError, ValueError):
            names = None
        _cache[self.url] = (time.time(), names)
        return names

    def locate(self) -> Optional[str]:
        """The server address when it is running AND the chosen model is installed (else None = 'not installed')."""
        models = self._models()
        if models is None:
            return None
        want = self.model if ":" in self.model else self.model + ":latest"
        return self.url if want in models or self.model in models else None

    def status(self) -> dict:
        models = self._models()
        return {"running": models is not None, "model": self.model, "installed": models is not None and self.locate() is not None,
                "models": models or [], "url": self.url, "num_ctx": self.num_ctx}

    # ------------------------------------------------------------------ the call
    def build_argv(self, exe: str, write: bool) -> list[str]:  # pragma: no cover - HTTP, not a process
        raise NotImplementedError

    def run(self, prompt: str, cwd: str, write: bool, timeout: float, no_output_timeout: Optional[float] = None) -> ProviderResult:
        if write:
            raise ProviderError("ollama는 읽기 전용 역할(계획 검토·검수·분석 등)에만 쓸 수 있습니다.")
        if estimate_tokens(prompt) > self.num_ctx * 0.9:
            raise ProviderError(f"ollama: 프롬프트(약 {estimate_tokens(prompt):,} 토큰)가 문맥 길이({self.num_ctx:,})보다 길어 잘릴 수 있습니다. "
                                "설정 [ollama] num_ctx 를 늘리거나(메모리 사용 증가) 더 짧은 입력으로 쓰세요.")
        body = {"model": self.model, "stream": False, "think": False, "keep_alive": self.keep_alive,
                "options": {"temperature": 0, "num_ctx": self.num_ctx}, "messages": [{"role": "user", "content": prompt}]}
        if wants_json(prompt):
            body["format"] = "json"
        req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json"})
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            if exc.code == 404:
                raise ProviderError(f"ollama: 모델 '{self.model}'이 설치돼 있지 않습니다. 설치: ollama pull {self.model}") from exc
            raise ProviderError(f"ollama가 오류를 돌려줬습니다 ({exc.code}): {detail}") from exc
        except TimeoutError as exc:
            raise ProviderTimeout(f"ollama: {timeout:.0f}초 안에 답하지 못했습니다 (모델이 크거나 메모리가 부족할 수 있습니다)") from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise ProviderTimeout(f"ollama: {timeout:.0f}초 안에 답하지 못했습니다") from exc
            _cache.pop(self.url, None)
            raise ProviderUnavailable(f"ollama 서버({self.url})에 연결할 수 없습니다. Ollama 앱이 실행 중인지 확인하세요.") from exc
        except (OSError, ValueError) as exc:
            raise ProviderError(f"ollama 응답을 읽지 못했습니다: {exc}") from exc
        text = _THINK.sub("", str((data.get("message") or {}).get("content", ""))).strip()
        if not text:
            raise ProviderError("ollama가 빈 답을 돌려줬습니다.")
        tin, tout = int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)
        if tin >= self.num_ctx * 0.98:
            raise ProviderError(f"ollama: 입력이 문맥 길이({self.num_ctx:,})를 꽉 채워 앞부분이 잘렸을 수 있어 결과를 쓰지 않습니다. num_ctx를 늘리세요.")
        return ProviderResult(text=text, exit_code=0, duration_sec=round(time.time() - started, 2), provider=self.name,
                              tokens={"in": tin, "out": tout, "total": tin + tout} if (tin or tout) else None)
