"""Server-side optional OpenAI-compatible provider; no tool execution."""
import os
import httpx
_runtime={}
def setting(name,default=None):return _runtime.get(name,os.getenv(name,default))

def install_settings(app):
    from pydantic import BaseModel,Field
    class Settings(BaseModel):
        base_url:str=Field(max_length=500)
        model:str=Field(max_length=120)
        api_key:str=Field('',max_length=1000)
        embedding_model:str=Field('',max_length=120)
    @app.get('/api/model/settings')
    def get_settings():
        return {'base_url':setting('LLM_BASE_URL','https://api.openai.com/v1'),'model':setting('LLM_MODEL',''),'configured':bool(setting('LLM_API_KEY')),'embedding_model':setting('EMBEDDING_MODEL','')}
    @app.post('/api/model/settings')
    def set_settings(body:Settings):
        from urllib.parse import urlsplit
        url=urlsplit(body.base_url)
        if not url.hostname or url.username or url.password or url.query or url.fragment or (url.scheme!='https' and not(url.scheme=='http' and url.hostname in {'127.0.0.1','localhost'})):raise HTTPException(400,'API地址需HTTPS或本地服务，不允许内嵌凭据')
        _runtime.update(LLM_BASE_URL=body.base_url.rstrip('/'),LLM_MODEL=body.model.strip(),EMBEDDING_MODEL=body.embedding_model.strip())
        if body.api_key:_runtime['LLM_API_KEY']=body.api_key
        return {'ok':True,'notice':'配置仅在内存保存，重启后使用环境变量；没有发起付费请求'}
from fastapi import HTTPException


def provider_status():
    return {"configured": bool(setting("LLM_API_KEY") and setting("LLM_MODEL")),
            "model": setting("LLM_MODEL", "未配置")}


def generate(system, prompt):
    key = setting("LLM_API_KEY")
    model = setting("LLM_MODEL")
    if not key or not model:
        raise HTTPException(503, "请在服务器设置 LLM_API_KEY 和 LLM_MODEL；核心功能无需模型")
    base = setting("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    from urllib.parse import urlsplit
    url=urlsplit(base)
    if not url.hostname or url.username or url.password or url.query or url.fragment or (url.scheme!="https" and not (url.scheme=="http" and url.hostname in {"127.0.0.1","localhost"})):
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
