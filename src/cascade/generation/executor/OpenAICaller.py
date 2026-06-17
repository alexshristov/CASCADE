import os
import time
from openai import OpenAI
from cascade.generation.executor.LLMCaller import LLMCaller
from cascade.utils.Metrics import extract_finish_reason, extract_response_usage, record_metric_event


class OpenAICaller(LLMCaller):
    def __init__(
        self,
        max_attempts=1,
        max_tokens=16000,
        temperature=0,
        delay=5,
        dummy=False,
        model="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        freq_penalty=0.0,
        base_url=None,          # ← optional
        api_key=None,           # ← optional
        timeout=60.0,
    ):
        self.max_attempts = max_attempts
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.delay = delay
        self.freq_penalty = freq_penalty
        self.model = model
        self.base_url = base_url

        if dummy:
            self.client = None
            return

        client_kwargs = {
            "timeout": timeout,
        }

        if base_url is not None:
            # we expect this to be a vLLM OpenAI-compatible server
            client_kwargs["base_url"] = base_url
            client_kwargs["api_key"] = os.environ.get("VLLM_API_KEY", api_key or "dummy")
        else:
            # Normal OpenAI
            client_kwargs["api_key"] = os.environ.get("OPENAI_API_KEY", api_key)

        self.client = OpenAI(**client_kwargs)

    def execute(self, prompt, **kwargs):
        attempt = 0
        while attempt < self.max_attempts:
            attempt_number = attempt + 1
            start = time.perf_counter()
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=prompt,
                    max_completion_tokens=self.max_tokens,   #temporary fix for gpt5
                    #temperature=self.temperature,
                    frequency_penalty=self.freq_penalty,
                    **kwargs,
                )
                elapsed = time.perf_counter() - start
                usage = extract_response_usage(response)
                record_metric_event(
                    "llm_call",
                    attempt=attempt_number,
                    model=self.model,
                    base_url=self.base_url,
                    max_tokens=self.max_tokens,
                    success=True,
                    elapsed_seconds=elapsed,
                    finish_reason=extract_finish_reason(response),
                    **usage,
                )
                return response

            except Exception as e:
                elapsed = time.perf_counter() - start
                record_metric_event(
                    "llm_call",
                    attempt=attempt_number,
                    model=self.model,
                    base_url=self.base_url,
                    max_tokens=self.max_tokens,
                    success=False,
                    elapsed_seconds=elapsed,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                )
                print(f"Generation attempt {attempt_number} failed: {e}")
                attempt += 1
                time.sleep(self.delay)

        raise Exception("Generation failed. because of repeated errors.")
