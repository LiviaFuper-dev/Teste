"""
utils/logs.py — Coleta e envia histórico de conversa de uma thread.
"""

import io
import re

import discord

_MENTION_RE = re.compile(r"<@!?(\d+)>")
_ROLE_RE = re.compile(r"<@&(\d+)>")
_CHANNEL_RE = re.compile(r"<#(\d+)>")
_MD_RE = re.compile(r"[*_~`|>]")


def _limpar_texto(texto: str, guild: discord.Guild | None = None) -> str:
    """Remove markdown e resolve mentions para texto limpo."""
    def _resolve_mention(m):
        uid = int(m.group(1))
        if guild:
            member = guild.get_member(uid)
            if member:
                return f"@{member.display_name}"
        return f"@{uid}"

    def _resolve_role(m):
        rid = int(m.group(1))
        if guild:
            role = guild.get_role(rid)
            if role:
                return f"@{role.name}"
        return f"@cargo-{rid}"

    def _resolve_channel(m):
        cid = int(m.group(1))
        if guild:
            ch = guild.get_channel(cid)
            if ch:
                return f"#{ch.name}"
        return f"#canal-{cid}"

    texto = _MENTION_RE.sub(_resolve_mention, texto)
    texto = _ROLE_RE.sub(_resolve_role, texto)
    texto = _CHANNEL_RE.sub(_resolve_channel, texto)
    texto = _MD_RE.sub("", texto)
    return texto.strip()


async def coletar_historico(thread: discord.Thread) -> str:
    """Coleta todas as mensagens da thread e retorna como texto limpo."""
    guild = thread.guild
    linhas: list[str] = []
    try:
        async for msg in thread.history(oldest_first=True, limit=None):
            texto = _limpar_texto(msg.content, guild)
            if not texto and not msg.attachments:
                continue
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            if texto:
                linhas.append(f"[{ts}] {msg.author.display_name}: {texto}")
            for att in msg.attachments:
                linhas.append(f"[{ts}] {msg.author.display_name}: [Anexo] {att.url}")
    except Exception as e:
        print(f"[LOG] Erro ao ler historico de '{thread.name}': {e}")
    return "\n".join(linhas)


async def enviar_log_conversa(
    thread: discord.Thread,
    guild: discord.Guild,
    canal_logs_id: int,
    prefixo_log: str = "\U0001f4c1",
    header_extra: str = "",
) -> bool:
    """Envia o histórico da thread como arquivo .txt para o canal de logs."""
    log_text = f"Log da Thread: {thread.name}\n\n"
    if header_extra:
        log_text += header_extra + "\n\n"
    log_text += "=== Mensagens da Thread ===\n\n"
    log_text += await coletar_historico(thread)

    logs_canal = guild.get_channel(canal_logs_id)
    if not logs_canal:
        print(f"[LOG] Canal de logs {canal_logs_id} nao encontrado (guild {guild.id})")
        return False

    log_file = discord.File(
        io.BytesIO(log_text.encode("utf-8")),
        filename=f"log_{thread.name}.txt",
    )
    try:
        await logs_canal.send(
            content=f"{prefixo_log} Log do chamado `{thread.name}`",
            file=log_file,
        )
        print(f"[LOG] Enviado para #{logs_canal.name}: '{thread.name}'")
        return True
    except Exception as e:
        print(f"[LOG] Erro ao enviar log de '{thread.name}': {e}")
        return False
