"""
utils/pending_chamados.py — Painel de chamados pendentes por inatividade.

Fluxo:
  - Chamados de Sistemas, Equipamentos/TI e Recuperar Contato ficam ativos na thread original.
  - Após 8h sem interação, o main.py gera log, cria um card em #chamados-pendentes
    e arquiva a thread original.
  - O card permite ver histórico, retomar o chamado em uma nova thread ou excluir o pendente.
  - Ao retomar, dados importantes do atendimento anterior são restaurados para o fluxo continuar.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import re
from pathlib import Path
from typing import Any

import discord

import config
from utils.thread_utils import safe_join_thread

_PENDING_FILE = Path("data/chamados_pendentes.json")
_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
_BR_TIMEZONE = datetime.timezone(datetime.timedelta(hours=-3))
_PENDING_WARNING_TEXT = "Este chamado ficou 8 horas sem interação e foi movido para o painel de chamados pendentes."


def _now_brasilia() -> datetime.datetime:
    return datetime.datetime.now(_BR_TIMEZONE)


def _load_pending() -> dict[str, dict[str, Any]]:
    if not _PENDING_FILE.exists():
        return {}
    try:
        raw = json.loads(_PENDING_FILE.read_text(encoding="utf-8"))
        return {str(k): v for k, v in raw.items()}
    except Exception as e:
        print(f"[PENDENTES] Erro ao carregar pendencias: {e}")
        return {}


def _save_pending(data: dict[str, dict[str, Any]]) -> None:
    try:
        _PENDING_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[PENDENTES] Erro ao salvar pendencias: {e}")


def _pending_channel_id(guild_id: int) -> int | None:
    """Retorna o canal configurado para a fila visual de chamados pendentes."""
    cfg = config.SERVIDORES.get(guild_id, {})
    return cfg.get("canal_chamados_pendentes") or cfg.get("canal_pendentes")


def _attachments_archive_channel(guild: discord.Guild) -> discord.abc.Messageable | None:
    # O canal de logs serve somente como base para criar topicos privados.
    # Assim, o backup das imagens nao fica exposto diretamente no canal de logs.
    channel_id = config.SERVIDORES.get(guild.id, {}).get("canal_logs")
    return guild.get_channel(channel_id) if channel_id else None


def chamado_pendente_existe(original_thread_id: int) -> bool:
    pending = _load_pending()
    for existing in pending.values():
        if int(existing.get("original_thread_id", 0)) == original_thread_id:
            return True
    return False


def _split_contato_motivo(motivo: str) -> tuple[str, str | None, str | None]:
    resumo_linhas: list[str] = []
    nome = None
    cpf = None
    for line in motivo.splitlines():
        clean = line.strip()
        lower = clean.lower()
        if lower.startswith("nome do contato:"):
            nome = clean.split(":", 1)[1].strip()
        elif lower.startswith("cpf do contato:"):
            cpf = clean.split(":", 1)[1].strip()
        elif clean and not lower.startswith("dados do contato"):
            resumo_linhas.append(clean)
    resumo = " ".join(resumo_linhas).strip() or "Sem resumo registrado."
    return resumo, nome, cpf


def _nivel_urgencia_equipamentos(record: dict[str, Any]) -> str | None:
    original_name = record.get("thread_name") or record.get("original_thread_name") or ""
    parts = [part.strip() for part in original_name.split(" - ")]
    if len(parts) >= 2 and parts[0] == "2":
        return parts[1]
    return None


def _display_sistema(record: dict[str, Any]) -> str:
    sistema = str(record.get("sistema") or "-")
    original_name = record.get("thread_name") or record.get("original_thread_name") or ""
    parts = [part.strip() for part in original_name.split(" - ")]
    if len(parts) >= 2 and parts[0] == "1":
        return parts[1]
    if sistema in {"Automações", "Automacoes"}:
        return "Robôs/Planilhas"
    return sistema


def _format_created_at(value: str | None) -> str:
    if not value:
        return "-"
    try:
        created_at = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return created_at.astimezone(_BR_TIMEZONE).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return value


def _thread_created_at_iso(thread: discord.Thread) -> str:
    created_at = thread.created_at or datetime.datetime.now(datetime.timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=datetime.timezone.utc)
    return created_at.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _first_history_timestamp(record: dict[str, Any]) -> str | None:
    for line in record.get("conversation_history") or []:
        match = re.match(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", str(line))
        if match:
            try:
                dt = datetime.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=_BR_TIMEZONE).astimezone(datetime.timezone.utc)
                return dt.isoformat().replace("+00:00", "Z")
            except Exception:
                return None
    return None


def _original_opened_at(record: dict[str, Any]) -> str | None:
    # Cards antigos salvavam "created_at" como a data em que o chamado caiu em pendentes.
    # Para nao mostrar essa data como abertura do chamado, preferimos o campo novo ou
    # a primeira mensagem util salva no historico.
    return (
        record.get("original_opened_at")
        or _first_history_timestamp(record)
        or record.get("created_at")
    )


def _log_line(label: str, value: Any, *, limit: int = 700) -> str:
    text = str(value or "-").replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return f"{label}: {text}"


def _message_author_name(message: discord.Message) -> str:
    return getattr(message.author, "display_name", None) or message.author.name


def _message_timestamp(message: discord.Message) -> str:
    return message.created_at.astimezone(_BR_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _replace_mentions(message: discord.Message, text: str) -> str:
    for role in message.role_mentions:
        text = text.replace(f"<@&{role.id}>", f"@{role.name}")
    for user in message.mentions:
        text = text.replace(f"<@{user.id}>", f"@{user.display_name}")
        text = text.replace(f"<@!{user.id}>", f"@{user.display_name}")
    return text


def _safe_link_label(value: str) -> str:
    return value.replace("[", "(").replace("]", ")").replace("\n", " ").strip() or "arquivo"


def _message_text(message: discord.Message) -> str:
    parts: list[str] = []
    content = (message.content or "").replace("\r", "").strip()
    if content:
        parts.append(content)

    if message.embeds:
        embed = message.embeds[0]
        embed_parts = [
            (embed.title or "").strip(),
            (embed.description or "").strip(),
        ]
        for field in embed.fields[:3]:
            embed_parts.append(f"{field.name}: {field.value}".strip())
        embed_text = "\n".join(part for part in embed_parts if part).strip()
        if embed_text:
            parts.append(embed_text)

    if message.attachments:
        nomes = ", ".join(attachment.filename for attachment in message.attachments)
        parts.append(f"[anexo enviado] {nomes}")

    text = "\n".join(parts).strip() or "[mensagem sem texto]"
    return _replace_mentions(message, text).replace("```", "'''")


def _is_automatic_opening_message(message: discord.Message, text: str) -> bool:
    if not message.author.bot:
        return False

    clean = text.strip()
    if clean == "[mensagem sem texto]":
        return True
    if clean.startswith("**Solicitação de ") or clean.startswith("Solicitação de "):
        return True
    if "Chamado aberto" in clean and (
        "Todos os participantes podem conversar" in clean
        or "Este tópico será arquivado" in clean
    ):
        return True
    return False


async def _capture_thread_history(
    thread: discord.Thread,
    *,
    limit: int | None = None,
) -> list[str]:
    linhas: list[str] = []
    warning_seen = False
    try:
        async for message in thread.history(limit=limit, oldest_first=True):
            text = _message_text(message)
            if _is_automatic_opening_message(message, text):
                continue
            if _PENDING_WARNING_TEXT in text:
                if warning_seen:
                    continue
                warning_seen = True
            if len(text) > 900:
                text = text[:897] + "..."
            linhas.append(f"[{_message_timestamp(message)}] {_message_author_name(message)}: {text}")
    except Exception as e:
        print(f"[PENDENTES] Erro ao capturar historico da thread {thread.id}: {e}")
    return linhas


async def _archive_attachment(
    archive_channel: discord.abc.Messageable | None,
    *,
    thread: discord.Thread,
    message: discord.Message,
    attachment: discord.Attachment,
) -> str:
    if archive_channel is None:
        return attachment.url

    try:
        storage_thread = await archive_channel.create_thread(
            name=f"anexo-{thread.name[:55]}-{message.id}",
            type=discord.ChannelType.private_thread,
            auto_archive_duration=60,
            invitable=False,
        )
        file = await attachment.to_file(use_cached=True)
        sent = await storage_thread.send(
            content=(
                f"Backup de anexo do chamado `{thread.name}` "
                f"({getattr(thread, 'jump_url', 'sem link')})\n"
                f"Autor: {_message_author_name(message)}"
            ),
            file=file,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            await storage_thread.edit(archived=True, locked=True)
        except Exception as e:
            print(
                f"[PENDENTES] Nao foi possivel arquivar topico de anexo "
                f"{storage_thread.id}: {e}"
            )
        if sent.attachments:
            return sent.attachments[0].url
    except Exception as e:
        print(
            f"[PENDENTES] Erro ao arquivar anexo {attachment.filename} "
            f"da thread {thread.id}: {e}"
        )
    return attachment.url


async def _capture_thread_attachments(
    thread: discord.Thread,
    guild: discord.Guild,
    *,
    limit: int | None = None,
) -> list[dict[str, str]]:
    arquivos: list[dict[str, str]] = []
    archive_channel = _attachments_archive_channel(guild)
    try:
        async for message in thread.history(limit=limit, oldest_first=True):
            if not message.attachments:
                continue
            timestamp = _message_timestamp(message)
            author = _message_author_name(message)
            for attachment in message.attachments:
                url = await _archive_attachment(
                    archive_channel,
                    thread=thread,
                    message=message,
                    attachment=attachment,
                )
                arquivos.append(
                    {
                        "label": f"{timestamp} - {author} - {attachment.filename}",
                        "url": url,
                    }
                )
                if len(arquivos) >= 25:
                    return arquivos
    except Exception as e:
        print(f"[PENDENTES] Erro ao capturar anexos da thread {thread.id}: {e}")
    return arquivos


def _build_history_log(record: dict[str, Any]) -> str:
    linhas = list(record.get("conversation_history") or [])
    if not linhas:
        linhas = [
            "Historico da conversa nao foi salvo para este card.",
            _log_line("Criado em", _format_created_at(record.get("created_at"))),
            _log_line("Motivo", record.get("motivo") or "Sem resumo registrado."),
        ]

    return "=== Mensagens da Thread ===\n\n" + "\n".join(linhas)


def _resume_history_line(raw_line: str) -> str | None:
    line = str(raw_line or "").strip()
    if not line:
        return None

    if "] " in line:
        line = line.split("] ", 1)[1].strip()
    if ": " not in line:
        return None

    author, text = line.split(": ", 1)
    author = author.strip()
    text = text.replace("\n", " ").strip()
    lower_text = text.lower()
    lower_author = author.lower()

    ignored_fragments = [
        "[mensagem sem texto]",
        "chamado aberto",
        "chamado reaberto",
        "solicitação de",
        "solicitacao de",
        "descrição do solicitante",
        "descricao do solicitante",
        "nível de urgência",
        "nivel de urgencia",
        "todos os participantes podem conversar",
        "somente o t.i. pode arquivar",
        "este tópico será arquivado",
        "este topico sera arquivado",
        "ficou 8 horas sem interação",
        "ficou 24 horas sem interação",
        "foi movido para o painel de chamados pendentes",
    ]
    if lower_author.startswith("caveira"):
        return None
    if any(fragment in lower_text for fragment in ignored_fragments):
        return None
    if text.startswith("@") and len(text.split()) <= 2:
        return None

    if len(text) > 260:
        text = text[:257] + "..."
    return f"{author}: {text}"


def _resume_history_text(record: dict[str, Any], *, limit: int = 12) -> str:
    linhas = [
        clean
        for raw in (record.get("conversation_history") or [])
        if (clean := _resume_history_line(str(raw)))
    ]
    if not linhas:
        return ""

    historico = "\n".join(linhas[-limit:])
    if len(historico) > 1700:
        historico = historico[-1697:] + "..."
    return f"**Histórico do chamado anterior:**\n```text\n{historico}\n```"


def _resume_attachments_text(record: dict[str, Any], *, limit: int = 5) -> str:
    arquivos = record.get("conversation_attachments") or []
    linhas: list[str] = []
    for arquivo in arquivos[:limit]:
        label = _safe_link_label(str(arquivo.get("label") or "arquivo"))
        url = str(arquivo.get("url") or "").strip()
        if url:
            linhas.append(f"- [{label}]({url})")

    if not linhas:
        return ""
    return "**Anexos do chamado anterior:**\n" + "\n".join(linhas)


def _discord_message_chunks(text: str, *, limit: int = 1900) -> list[str]:
    """Divide textos longos em blocos seguros para mensagens do Discord."""
    chunks: list[str] = []
    current = ""
    for line in text.strip().splitlines():
        pieces = [
            line[index:index + limit]
            for index in range(0, len(line), limit)
        ] or [""]
        for piece in pieces:
            candidate = f"{current}\n{piece}" if current else piece
            if current and len(candidate) > limit:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


async def _send_reopened_content(
    thread: discord.Thread,
    summary: str,
    history: str = "",
    attachments: str = "",
) -> None:
    """Mantem uma mensagem curta unida e divide apenas quando ultrapassa o limite."""
    sections = [
        section.strip()
        for section in (summary, history, attachments)
        if section.strip()
    ]
    combined = "\n\n".join(sections)
    if len(combined) <= 1900:
        await thread.send(combined)
        return

    for section in sections:
        for chunk in _discord_message_chunks(section):
            await thread.send(chunk)


def _historico_anterior_text(record: dict[str, Any]) -> str:
    log_url = record.get("log_url")
    if log_url:
        return f"**Histórico anterior:** {log_url}"
    return "**Histórico anterior:** #logs"


def _opened_at_text(record: dict[str, Any]) -> str:
    return f"**Chamado original aberto em:** {_format_created_at(_original_opened_at(record))}"


def _build_embed(record: dict[str, Any]) -> discord.Embed:
    """Monta o card exibido em #chamados-pendentes."""
    motivo = record.get("motivo") or "Sem resumo registrado."
    resumo, nome_contato, cpf_contato = _split_contato_motivo(motivo)
    if len(resumo) > 900:
        resumo = resumo[:897] + "..."

    sistema = _display_sistema(record)
    if record.get("kind") == "contato":
        sistema = "Recuperador Contato"

    log_url = record.get("log_url")
    historico = f"[#logs]({log_url})" if log_url else "#logs"

    linhas = [
        f"**User:** {record.get('user_name') or 'Usuario nao identificado'}",
        "",
        f"**Sistema:** {sistema}",
    ]
    if record.get("kind") == "ti":
        nivel = _nivel_urgencia_equipamentos(record)
        if nivel:
            linhas.append(f"**Nível de urgência:** {nivel}")
    linhas.extend([
        "**Status:** Aguardando retomada",
        f"**Histórico anterior:** {historico}",
        "",
    ])

    steps = (record.get("payload") or {}).get("steps", {})
    if record.get("kind") == "sistemas" and sistema in {"E-mail", "Google Drive"}:
        linhas.extend(
            [
                f"**Resumo:** Chamado ficou 8h sem interação.",
                f"**E-mail selecionado:** {steps.get('dominio_detectado', '-')}",
                f"**E-mail informado:** {steps.get('email_usuario', '-')}",
                f"**Problema relatado:** {steps.get('problema', '-')}",
            ]
        )
    else:
        linhas.append(f"**Resumo:** {resumo}")

    if record.get("kind") == "contato":
        if nome_contato:
            linhas.extend(["", f"**Nome do contato:** {nome_contato}"])
        if cpf_contato:
            linhas.append(f"**CPF do contato:** {cpf_contato}")

    if record.get("history_visible"):
        linhas.extend(["", "**Histórico:**", f"```text\n{_build_history_log(record)}\n```"])
        arquivos = record.get("conversation_attachments") or []
        if arquivos:
            linhas.extend(["", "**Arquivos enviados:**"])
            for arquivo in arquivos[:10]:
                label = _safe_link_label(str(arquivo.get("label") or "arquivo"))
                url = str(arquivo.get("url") or "").strip()
                if url:
                    linhas.append(f"- [{label}]({url})")

    return discord.Embed(
        title="Chamado Não Resolvido",
        description="\n".join(linhas),
        color=discord.Color.orange(),
        timestamp=_now_brasilia(),
    )


async def _delete_ephemeral_after(interaction: discord.Interaction, delay: int = 8) -> None:
    await asyncio.sleep(delay)
    try:
        await interaction.delete_original_response()
    except Exception:
        pass


def _history_pages(record: dict[str, Any], *, max_chars: int = 3400) -> list[str]:
    """Divide mensagens e anexos em paginas seguras para embeds do Discord."""
    entries = [
        str(line).strip()
        for line in record.get("conversation_history") or []
        if str(line).strip()
    ]
    if not entries:
        entries = ["Historico da conversa nao foi salvo para este card."]

    arquivos = record.get("conversation_attachments") or []
    if arquivos:
        entries.extend(["", "**Arquivos enviados:**"])
        for arquivo in arquivos:
            label = _safe_link_label(str(arquivo.get("label") or "arquivo"))
            url = str(arquivo.get("url") or "").strip()
            if url:
                entries.append(f"- [{label}]({url})")

    pages: list[str] = []
    current = ""
    for entry in entries:
        parts = [
            entry[index:index + max_chars]
            for index in range(0, len(entry), max_chars)
        ] or [""]
        for part in parts:
            candidate = f"{current}\n{part}".strip() if current else part
            if current and len(candidate) > max_chars:
                pages.append(current)
                current = part
            else:
                current = candidate
    if current:
        pages.append(current)
    return pages or ["Historico vazio."]


def _history_page_embed(
    record: dict[str, Any],
    pages: list[str],
    index: int,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Historico - {_display_sistema(record)}",
        description=pages[index],
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Pagina {index + 1} de {len(pages)}")
    return embed


class PendingHistoryView(discord.ui.View):
    """Paginacao privada para historicos que nao cabem no card publico."""

    def __init__(self, record: dict[str, Any], owner_id: int):
        super().__init__(timeout=300)
        self.record = record
        self.owner_id = owner_id
        self.pages = _history_pages(record)
        self.index = 0
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.previous_page.disabled = self.index == 0
        self.next_page.disabled = self.index >= len(self.pages) - 1

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Abra o historico pelo botao do card para navegar nas suas paginas.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="◀ Anterior", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._check_owner(interaction):
            return
        self.index = max(0, self.index - 1)
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=_history_page_embed(self.record, self.pages, self.index),
            view=self,
        )

    @discord.ui.button(label="Proxima ▶", style=discord.ButtonStyle.secondary)
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._check_owner(interaction):
            return
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=_history_page_embed(self.record, self.pages, self.index),
            view=self,
        )


class PendingChamadoView(discord.ui.View):
    """Botões persistentes usados nos cards do painel de chamados pendentes."""

    def __init__(self, bot: discord.Client | None = None, history_visible: bool = False):
        super().__init__(timeout=None)
        self.bot = bot
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "pendentes_ver_historico":
                item.label = "▲ Ocultar Histórico" if history_visible else "▼ Ver Histórico"

    @discord.ui.button(
        label="▼ Ver Histórico",
        style=discord.ButtonStyle.secondary,
        custom_id="pendentes_ver_historico",
    )
    async def ver_historico(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = _load_pending()
        record_key = str(interaction.message.id)
        record = pending.get(record_key)
        if not record:
            await interaction.response.send_message(
                "Nao encontrei mais os dados desse chamado.", ephemeral=True
            )
            await _delete_ephemeral_after(interaction)
            return

        history_is_open = any(
            "**Histórico:**" in (embed.description or "")
            for embed in interaction.message.embeds
        )
        if history_is_open:
            record["history_visible"] = False
            pending[record_key] = record
            _save_pending(pending)
            await interaction.response.edit_message(
                embed=_build_embed(record),
                view=PendingChamadoView(self.bot, False),
            )
            return

        expanded_record = dict(record)
        expanded_record["history_visible"] = True
        expanded_embed = _build_embed(expanded_record)

        # Mantem o historico dentro do card sempre que ele couber por inteiro.
        if len(expanded_embed.description or "") <= 4096 and len(expanded_embed) <= 6000:
            record["history_visible"] = True
            pending[record_key] = record
            _save_pending(pending)
            await interaction.response.edit_message(
                embed=expanded_embed,
                view=PendingChamadoView(self.bot, True),
            )
            return

        # Se ultrapassar o limite, abre o historico completo em paginas separadas.
        pages_view = PendingHistoryView(record, interaction.user.id)
        await interaction.response.send_message(
            embed=_history_page_embed(record, pages_view.pages, 0),
            view=pages_view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="Retomar Chamado",
        style=discord.ButtonStyle.primary,
        custom_id="pendentes_retomar_chamado",
    )
    async def retomar_chamado(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = _load_pending()
        record = pending.get(str(interaction.message.id))
        if not record:
            await interaction.response.send_message(
                "Nao encontrei mais os dados desse chamado.", ephemeral=True
            )
            await _delete_ephemeral_after(interaction)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("Servidor nao identificado.", ephemeral=True)
            return

        channel_id = config.SERVIDORES.get(guild.id, {}).get("canal_unificado")
        parent = guild.get_channel(channel_id) if channel_id else None
        if parent is None:
            await interaction.followup.send(
                "Canal de ajuda nao encontrado no config.py.", ephemeral=True
            )
            return

        user = guild.get_member(int(record["user_id"]))
        if user is None:
            try:
                user = await guild.fetch_member(int(record["user_id"]))
            except Exception:
                user = None

        thread_name = record.get("thread_name") or f"{record.get('prefixo', '1')} - Retomado"
        if not thread_name.lower().endswith("retomado"):
            thread_name = f"{thread_name} - retomado"
        if len(thread_name) > 90:
            thread_name = thread_name[:90]

        try:
            thread = await parent.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
            )
        except Exception as e:
            print(f"[PENDENTES] Erro ao retomar chamado: {e}")
            await interaction.followup.send("Nao consegui criar a nova thread.", ephemeral=True)
            return

        await safe_join_thread(interaction.client.user, thread)
        if user:
            try:
                await thread.add_user(user)
            except Exception:
                pass
        try:
            await thread.add_user(interaction.user)
        except Exception:
            pass

        if record.get("kind") == "sistemas" and record.get("payload"):
            try:
                from modules.sistemas._engine import set_payload
                # Reaproveita o payload original para o !sistema finalizar a nova thread.
                payload = record["payload"]
                payload["thread_id"] = thread.id
                payload["thread_name"] = thread.name
                payload["thread_url"] = getattr(thread, "jump_url", None)
                payload["retomado_de_thread_id"] = record.get("original_thread_id")
                set_payload(thread.id, payload)
            except Exception as e:
                print(f"[PENDENTES] Erro ao restaurar payload de sistemas: {e}")

        if record.get("kind") == "ti" and record.get("motivo"):
            try:
                from modules.ti import register_thread_motivo
                # Mantém o motivo original disponível para o !logs de Equipamentos/TI.
                register_thread_motivo(thread.id, record["motivo"])
            except Exception as e:
                print(f"[PENDENTES] Erro ao restaurar motivo de TI: {e}")

        if record.get("kind") == "contato":
            try:
                from modules.contato import register_thread_activity
                # Reativa o controle de atividade usado pelo fluxo de Recuperar Contato.
                register_thread_activity(thread.id)
            except Exception as e:
                print(f"[PENDENTES] Erro ao restaurar atividade de contato: {e}")

        user_label = user.mention if user else record.get("user_name", "usuario")
        resume_history = _resume_history_text(record)
        resume_attachments = _resume_attachments_text(record)
        if record.get("kind") == "contato":
            _, nome_contato, cpf_contato = _split_contato_motivo(record.get("motivo") or "")
            linhas = [
                f"📌 **Chamado Reaberto**\n\n"
                f"Olá {user_label}, estamos retomando seu chamado anterior.",
                "",
                _historico_anterior_text(record),
                _opened_at_text(record),
                "",
            ]
            if nome_contato:
                linhas.append(f"**Nome do contato:** {nome_contato}")
            if cpf_contato:
                linhas.append(f"**CPF do contato:** {cpf_contato}")
            await _send_reopened_content(
                thread,
                "\n".join(linhas),
                resume_history,
                resume_attachments,
            )
        elif record.get("kind") == "sistemas":
            sistema = _display_sistema(record)
            if sistema in {"E-mail", "Google Drive"}:
                steps = (record.get("payload") or {}).get("steps", {})
                summary = (
                    f"📌 **Chamado Reaberto**\n\n"
                    f"Olá {user_label}, estamos retomando seu chamado anterior.\n\n"
                    f"**Sistema:** {sistema}\n"
                    f"**Motivo:** Chamado ficou 8h sem interação.\n"
                    f"{_historico_anterior_text(record)}\n"
                    f"{_opened_at_text(record)}\n"
                    f"**E-mail selecionado:** {steps.get('dominio_detectado', '-')}\n"
                    f"**E-mail informado:** {steps.get('email_usuario', '-')}\n"
                    f"**Problema relatado:** {steps.get('problema', '-')}"
                )
                await _send_reopened_content(
                    thread,
                    summary,
                    resume_history,
                    resume_attachments,
                )
            else:
                summary = (
                    f"📌 **Chamado Reaberto**\n\n"
                    f"Olá {user_label}, estamos retomando seu chamado anterior.\n\n"
                    f"**Sistema:** {sistema}\n"
                    f"**Motivo:** {record.get('motivo') or 'Sem resumo registrado.'}\n"
                    f"{_historico_anterior_text(record)}\n"
                    f"{_opened_at_text(record)}"
                )
                await _send_reopened_content(
                    thread,
                    summary,
                    resume_history,
                    resume_attachments,
                )
        elif record.get("kind") == "ti":
            nivel = "-"
            original_name = record.get("thread_name") or record.get("original_thread_name") or ""
            parts = [part.strip() for part in original_name.split(" - ")]
            if len(parts) >= 2 and parts[0] == "2":
                nivel = parts[1]
            summary = (
                f"📌 **Chamado Reaberto**\n\n"
                f"Olá {user_label}, estamos retomando seu chamado anterior.\n\n"
                f"**Sistema:** {record.get('sistema', 'Equipamentos')}\n"
                f"**Motivo:** {record.get('motivo') or 'Sem resumo registrado.'}\n"
                f"**Nível de urgência:** {nivel}\n"
                f"{_historico_anterior_text(record)}\n"
                f"{_opened_at_text(record)}"
            )
            await _send_reopened_content(
                thread,
                summary,
                resume_history,
                resume_attachments,
            )
        else:
            summary = (
                f"Olá {user_label}, estamos retomando seu chamado anterior.\n\n"
                f"Resumo:\n"
                f"- Tipo: {record.get('tipo_label', 'Chamado')}\n"
                f"- Sistema: {record.get('sistema', '-')}\n"
                f"- Motivo: {record.get('motivo') or 'Sem resumo registrado.'}\n"
                f"- Histórico anterior: {record.get('log_url') or '#logs'}\n"
                f"- Chamado original aberto em: {_format_created_at(_original_opened_at(record))}\n"
                "O atendimento continua por aqui e sera concluido somente com o comando adequado."
            )
            await _send_reopened_content(
                thread,
                summary,
                resume_history,
                resume_attachments,
            )

        responsavel_role_id = record.get("responsavel_role_id")
        if responsavel_role_id:
            await thread.send(
                f"<@&{responsavel_role_id}>",
                allowed_mentions=discord.AllowedMentions(roles=True),
            )

        pending.pop(str(interaction.message.id), None)
        _save_pending(pending)

        try:
            await interaction.message.delete()
        except Exception:
            try:
                await interaction.message.edit(content="Chamado retomado.", embed=None, view=None)
            except Exception:
                pass

        msg = await interaction.followup.send(
            f"Chamado retomado: {thread.mention}", ephemeral=True
        )
        await asyncio.sleep(8)
        try:
            await msg.delete()
        except Exception:
            pass

    @discord.ui.button(
        label="Excluir Chamado",
        style=discord.ButtonStyle.danger,
        custom_id="pendentes_excluir_chamado",
    )
    async def excluir_chamado(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = _load_pending()
        record = pending.pop(str(interaction.message.id), None)
        if record is None:
            await interaction.response.send_message(
                "Esse chamado ja nao estava mais registrado como pendente.", ephemeral=True
            )
            await _delete_ephemeral_after(interaction)
            return

        _save_pending(pending)
        try:
            await interaction.message.delete()
        except Exception:
            try:
                await interaction.message.edit(content="Chamado excluido do painel.", embed=None, view=None)
            except Exception:
                pass

        await interaction.response.send_message(
            "Chamado removido de #chamados-pendentes.", ephemeral=True
        )
        await _delete_ephemeral_after(interaction)


async def criar_card_pendente(
    thread: discord.Thread,
    guild: discord.Guild,
    *,
    kind: str,
    tipo_label: str,
    sistema: str,
    user_id: int,
    user_name: str,
    motivo: str,
    log_url: str | None,
    payload: dict[str, Any] | None = None,
    responsavel_role_id: int | None = None,
) -> bool:
    """Cria o card persistente no painel e salva os dados para retomada posterior."""
    channel_id = _pending_channel_id(guild.id)
    pending_channel = guild.get_channel(channel_id) if channel_id else None
    if pending_channel is None:
        print(f"[PENDENTES] Canal de chamados pendentes nao configurado para guild {guild.id}")
        return False

    pending = _load_pending()
    for existing in pending.values():
        if int(existing.get("original_thread_id", 0)) == thread.id:
            print(f"[PENDENTES] Card ja existente para thread {thread.id}; ignorando duplicado.")
            return True

    conversation_history = await _capture_thread_history(thread)
    conversation_attachments = await _capture_thread_attachments(thread, guild)
    bot_name = getattr(guild.me, "display_name", None) or getattr(guild.me, "name", "Bot")
    bot_timestamp = _now_brasilia().strftime("%Y-%m-%d %H:%M:%S")
    if not any(_PENDING_WARNING_TEXT in line for line in conversation_history):
        conversation_history.append(
            f"[{bot_timestamp}] {bot_name}: ⚠️ {_PENDING_WARNING_TEXT}"
        )

    record = {
        "kind": kind,
        "tipo_label": tipo_label,
        "sistema": sistema,
        "user_id": user_id,
        "user_name": user_name,
        "motivo": motivo,
        "log_url": log_url,
        "guild_id": guild.id,
        "original_thread_id": thread.id,
        "original_thread_name": thread.name,
        "thread_name": thread.name,
        "thread_url": getattr(thread, "jump_url", None),
        "inativo_ha": "8h",
        "original_opened_at": _thread_created_at_iso(thread),
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "payload": payload,
        "responsavel_role_id": responsavel_role_id,
        "conversation_history": conversation_history,
        "conversation_attachments": conversation_attachments,
    }

    try:
        content = f"<@&{responsavel_role_id}>" if responsavel_role_id else None
        msg = await pending_channel.send(
            content=content,
            embed=_build_embed(record),
            view=PendingChamadoView(),
            silent=False,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
    except Exception as e:
        print(f"[PENDENTES] Erro ao criar card pendente: {e}")
        return False

    pending[str(msg.id)] = record
    _save_pending(pending)
    return True
