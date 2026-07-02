"""
utils/pending_chamados.py — Painel de chamados pendentes por inatividade.

Fluxo:
  - Chamados de Sistemas, Equipamentos/TI e Recuperar Contato ficam ativos na thread original.
  - Após 24h sem interação, o main.py gera log, cria um card em #chamados-pendentes
    e arquiva a thread original.
  - O card permite ver histórico, retomar o chamado em uma nova thread ou excluir o pendente.
  - Ao retomar, dados importantes do atendimento anterior são restaurados para o fluxo continuar.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path
from typing import Any

import discord

import config
from utils.thread_utils import safe_join_thread

_PENDING_FILE = Path("data/chamados_pendentes.json")
_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
_BR_TIMEZONE = datetime.timezone(datetime.timedelta(hours=-3))


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


def _build_embed(record: dict[str, Any]) -> discord.Embed:
    """Monta o card exibido em #chamados-pendentes."""
    motivo = record.get("motivo") or "Sem resumo registrado."
    resumo, nome_contato, cpf_contato = _split_contato_motivo(motivo)
    if len(resumo) > 900:
        resumo = resumo[:897] + "..."

    sistema = record.get("sistema", "-")
    if record.get("kind") == "contato":
        sistema = "Recuperador Contato"

    log_url = record.get("log_url")
    historico = f"[#logs]({log_url})" if log_url else "#logs"

    linhas = [
        f"**Sistema:** {sistema}",
        "**Status:** Aguardando retomada",
        f"**Histórico anterior:** {historico}",
        "",
    ]

    steps = (record.get("payload") or {}).get("steps", {})
    if record.get("kind") == "sistemas" and sistema in {"E-mail", "Google Drive"}:
        linhas.extend(
            [
                f"**Resumo:** Chamado ficou 24h sem interação.",
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


class PendingChamadoView(discord.ui.View):
    """Botões persistentes usados nos cards do painel de chamados pendentes."""

    def __init__(self, bot: discord.Client | None = None):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Ver Historico",
        style=discord.ButtonStyle.secondary,
        custom_id="pendentes_ver_historico",
    )
    async def ver_historico(self, interaction: discord.Interaction, button: discord.ui.Button):
        record = _load_pending().get(str(interaction.message.id))
        if not record:
            await interaction.response.send_message(
                "Nao encontrei mais os dados desse chamado.", ephemeral=True
            )
            await _delete_ephemeral_after(interaction)
            return

        log_url = record.get("log_url")
        if log_url:
            msg = f"Historico do chamado: {log_url}"
        else:
            msg = "Esse chamado ainda nao tem link de historico salvo no canal de logs."
        await interaction.response.send_message(msg, ephemeral=True)
        await _delete_ephemeral_after(interaction)

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
        if record.get("kind") == "contato":
            _, nome_contato, cpf_contato = _split_contato_motivo(record.get("motivo") or "")
            linhas = [
                f"📌 **Chamado Reaberto**\n\n"
                f"Olá {user_label}, estamos retomando seu chamado anterior.",
                "",
            ]
            if nome_contato:
                linhas.append(f"**Nome do contato:** {nome_contato}")
            if cpf_contato:
                linhas.append(f"**CPF do contato:** {cpf_contato}")
            await thread.send("\n".join(linhas))
        elif record.get("kind") == "sistemas":
            sistema = record.get("sistema", "-")
            if sistema in {"E-mail", "Google Drive"}:
                steps = (record.get("payload") or {}).get("steps", {})
                await thread.send(
                    f"📌 **Chamado Reaberto**\n\n"
                    f"Olá {user_label}, estamos retomando seu chamado anterior.\n\n"
                    f"**Sistema:** {sistema}\n"
                    f"**Motivo:** Chamado ficou 24h sem interação.\n"
                    f"**E-mail selecionado:** {steps.get('dominio_detectado', '-')}\n"
                    f"**E-mail informado:** {steps.get('email_usuario', '-')}\n"
                    f"**Problema relatado:** {steps.get('problema', '-')}\n"
                )
            else:
                await thread.send(
                    f"📌 **Chamado Reaberto**\n\n"
                    f"Olá {user_label}, estamos retomando seu chamado anterior.\n\n"
                    f"**Sistema:** {sistema}\n"
                    f"**Motivo:** {record.get('motivo') or 'Sem resumo registrado.'}\n"
                )
        elif record.get("kind") == "ti":
            nivel = "-"
            original_name = record.get("thread_name") or record.get("original_thread_name") or ""
            parts = [part.strip() for part in original_name.split(" - ")]
            if len(parts) >= 2 and parts[0] == "2":
                nivel = parts[1]
            await thread.send(
                f"📌 **Chamado Reaberto**\n\n"
                f"Olá {user_label}, estamos retomando seu chamado anterior.\n\n"
                f"**Sistema:** {record.get('sistema', 'Equipamentos')}\n"
                f"**Motivo:** {record.get('motivo') or 'Sem resumo registrado.'}\n"
                f"**Nível de urgência:** {nivel}\n"
            )
        else:
            await thread.send(
                f"Olá {user_label}, estamos retomando seu chamado anterior.\n\n"
                f"Resumo:\n"
                f"- Tipo: {record.get('tipo_label', 'Chamado')}\n"
                f"- Sistema: {record.get('sistema', '-')}\n"
                f"- Motivo: {record.get('motivo') or 'Sem resumo registrado.'}\n"
                "O atendimento continua por aqui e sera concluido somente com o comando adequado."
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
        "inativo_ha": "24h",
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "payload": payload,
    }

    try:
        msg = await pending_channel.send(
            embed=_build_embed(record),
            view=PendingChamadoView(),
            silent=True,
        )
    except Exception as e:
        print(f"[PENDENTES] Erro ao criar card pendente: {e}")
        return False

    pending[str(msg.id)] = record
    _save_pending(pending)
    return True
