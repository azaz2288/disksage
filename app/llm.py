"""Server-side optional OpenAI-compatible provider; no tool execution."""
import os
import httpx
from fastapi import HTTPException


def provider_status():
    return {"configured": bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL")),
            "model": os.getenv("LLM_MODEL", "未配置")}


def generate(system, prompt):
    key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    if not key or not model:
        raise HTTPException(503, "请在服务器设置 LLM_API_KEY 和 LLM_MODEL；核心功能无需模型")
    base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    if not base.startswith("https://") and not base.startswith("http://127.0.0.1:"):
        raise HTTPException(503, "模型地址必须为 HTTPS 或本地回环服务")
    try:
        with httpx.Client(timeout=45, follow_redirects=False, trust_env=False) as client:
            response = client.post(base + "/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                   json={"model": model, "messages": [
                                       {"role": "system", "content": system},
                                       {"role": "user", "content": prompt}]})
            response.raise_for_status()
            result = response.json()["choices"][0]["message"]["content"]
            if not isinstance(result, str) or not result.strip():
                raise ValueError("empty provider answer")
            return result[:20000]
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        raise HTTPException(502, "模型请求失败，请检查地址、模型、额度与网络；密钥不会出现在错误信息中") from None
