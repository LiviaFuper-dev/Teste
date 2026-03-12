"""
utils/n8n.py — Interface única para envio de payloads ao N8N.

Todos os módulos devem usar este arquivo em vez de chamar requests/aiohttp diretamente.
Isso garante logs padronizados e um único lugar para tratar erros de integração.
"""

import asyncio
import aiohttp
import requests


async def send(url: str, payload: dict) -> bool:
    """
    Envia payload para um webhook N8N de forma assíncrona.
    Retorna True se a requisição retornou 2xx.
    """
    if not url:
        print("[N8N WARN] URL vazia, envio ignorado.")
        return False

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                ok = 200 <= resp.status < 300
                if ok:
                    print(f"[N8N OK] {url} → {resp.status}")
                else:
                    body = await resp.text()
                    print(f"[N8N WARN] {url} → {resp.status}: {body[:200]}")
                return ok
    except asyncio.TimeoutError:
        print(f"[N8N ERRO] Timeout: {url}")
        return False
    except Exception as e:
        print(f"[N8N ERRO] {url}: {e}")
        return False


async def send_with_response(url: str, payload: dict) -> dict:
    """
    Igual a send(), mas retorna o dict completo da resposta.
    Útil para o módulo Contato que precisa ler o campo 'existe' da resposta.
    Formato do retorno: {"status": int, "ok": bool, "json": dict | None, "text": str | None}
    """
    if not url:
        print("[N8N WARN] URL vazia, envio ignorado.")
        return {"status": None, "ok": False, "error": "empty_url"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                status = resp.status
                ok = 200 <= status < 300
                try:
                    content = await resp.json()
                    result = {"status": status, "ok": ok, "json": content, "text": None}
                except Exception:
                    text = await resp.text()
                    result = {"status": status, "ok": ok, "json": None, "text": text}

                if ok:
                    print(f"[N8N OK] {url} → {status}")
                else:
                    print(f"[N8N WARN] {url} → {status}: {result.get('text') or result.get('json')}")
                return result

    except asyncio.TimeoutError:
        print(f"[N8N ERRO] Timeout: {url}")
        return {"status": None, "ok": False, "error": "timeout"}
    except Exception as e:
        print(f"[N8N ERRO] {url}: {e}")
        return {"status": None, "ok": False, "error": str(e)}


def send_sync(url: str, payload: dict) -> bool:
    """
    Versão síncrona via requests.
    Use apenas fora de contexto assíncrono — em ambientes async, prefira send().
    """
    if not url:
        print("[N8N WARN] URL vazia, envio ignorado.")
        return False
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print(f"[N8N OK] {url} → {r.status_code}")
        return True
    except Exception as e:
        print(f"[N8N WARN] {url}: {e}")
        return False