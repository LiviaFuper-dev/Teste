"""
_command.py — Comando !sistema e SectorSelectView.

Contém:
  - SectorSelectView      (seleção de setor do colaborador → envia N8N → deleta thread)
  - _remove_non_allowed() (remove membros sem cargo autorizado da thread)
  - setup()               (registra o comando !sistema no bot)
"""

import discord
from discord.ext import commands

import config
from utils.command_help import reply_commands_help
from utils.logs import coletar_historico, enviar_log_conversa
from ._engine import (
    PENDING_PAYLOADS,
    _allowed_roles,
    _empresa_clickup,
    _member_has_role,
    _send_to_n8n,
    pop_payload,
)


# ── SectorSelectView ──────────────────────────────────────────────────────────

class SectorSelectView(discord.ui.View):
    def __init__(self, guild_id: int, thread_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.thread_id = thread_id

        options = [
            discord.SelectOption(label=s, value=s)
            for s in ["Comercial", "Administrativo", "Jurídico", "Financeiro", "RH", "Marketing", "TI", "Todos"]
        ]
        self._select = discord.ui.Select(
            placeholder="Selecione o setor do colaborador",
            options=options,
            custom_id=f"sistemas_sector_{thread_id}",
            min_values=1,
            max_values=1,
        )
        self._select.callback = self._on_select
        self.add_item(self._select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Servidor não identificado.", ephemeral=True)
            return False
        allowed = _allowed_roles(guild.id)
        member = guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await guild.fetch_member(interaction.user.id)
            except Exception:
                member = None
        if _member_has_role(member, allowed):
            return True
        await interaction.response.send_message(
            "Apenas membros autorizados podem preencher este formulário.", ephemeral=True
        )
        return False

    async def _on_select(self, interaction: discord.Interaction):
        selected = self._select.values[0] if self._select.values else None
        await interaction.response.defer(ephemeral=True)

        payload = pop_payload(self.thread_id)
        if payload is None:
            await interaction.followup.send(
                "Payload não encontrado (já enviado?).", ephemeral=True
            )
            return

        # Complementa o payload antes do envio ao N8N/ClickUp.
        # Em E-mail/Google Drive, alguns dados ficam salvos em steps durante o fluxo;
        # aqui eles viram campos diretos para facilitar o mapeamento na automação.

        steps = payload.get("steps", {})
        if payload.get("system") in {"E-mail", "Google Drive"}:
            email_selecionado = steps.get("email_selecionado") or steps.get("dominio_detectado")
            payload["email_selecionado"] = email_selecionado
            payload["email_usuario"] = steps.get("email_usuario")
            payload["problema_relatado"] = steps.get("problema")
            print(f"[SISTEMAS] E-mail selecionado para ClickUp: {email_selecionado}")

        empresa_value, empresa_label = _empresa_clickup(self.guild_id)
        if empresa_value:
            payload["empresa"] = empresa_value
            payload["Empresa"] = empresa_label
            payload["empresa_label"] = empresa_label

        payload["setor"] = selected
        payload["conversa"] = await coletar_historico(interaction.channel)
        if payload.get("system") in {"E-mail", "Google Drive"}:
            payload["conversa"] += (
                "\n\n=== Dados do E-mail ===\n"
                f"E-mail selecionado: {payload.get('email_selecionado') or '-'}\n"
                f"E-mail informado: {payload.get('email_usuario') or '-'}\n"
                f"Problema relatado: {payload.get('problema_relatado') or '-'}"
            )
        ok = await _send_to_n8n(payload)
        if not ok:
            await interaction.followup.send(
                "Erro ao enviar para o N8N. Verifique os logs.", ephemeral=True
            )
            return

        thread = interaction.channel
        try:
            await thread.send(
                f"✅ Formulário enviado. Setor: **{selected}**. Encerrando o tópico."
            )
        except Exception:
            pass

        await interaction.followup.send("Encaminhado com sucesso.", ephemeral=True)
        self.stop()

        canal_logs_id = config.SERVIDORES.get(self.guild_id, {}).get("canal_logs")
        if canal_logs_id:
            await enviar_log_conversa(
                thread, interaction.guild, canal_logs_id,
                prefixo_log="⚙️",
                header_extra=f"=== Sistemas ===\nSetor: {selected}",
            )

        try:
            await thread.delete()
            print(f"[SISTEMAS] Thread {self.thread_id} deletada após envio.")
        except Exception as e:
            print(f"[SISTEMAS] Não foi possível deletar thread {self.thread_id}: {e}")


# ── Helpers do comando ────────────────────────────────────────────────────────

async def _remove_non_allowed(
    thread: discord.Thread, guild: discord.Guild, allowed: set[int]
) -> tuple[list, list]:
    removed, failed = [], []
    try:
        await thread.fetch_members()
    except Exception:
        pass
    for tm in thread.members:
        try:
            member = guild.get_member(tm.id)
            if member is None:
                member = await guild.fetch_member(tm.id)
            if member.bot:
                continue
            if not _member_has_role(member, allowed):
                await thread.remove_user(member)
                removed.append(member.id)
        except Exception as e:
            failed.append(tm.id)
            print(f"[SISTEMAS] Erro ao remover {tm.id}: {e}")
    return removed, failed


# ── setup ─────────────────────────────────────────────────────────────────────

def setup(bot: commands.Bot) -> None:
    @bot.command(name="sistema")
    async def sistema_cmd(ctx: commands.Context):
        guild = ctx.guild
        channel = ctx.channel

        if not guild or not isinstance(channel, discord.Thread):
            await reply_commands_help(
                ctx,
                "O comando `!sistema` só pode ser usado dentro de um tópico de Sistemas.",
            )
            return

        if not channel.name.startswith("1 -"):
            await reply_commands_help(
                ctx,
                "Este comando só funciona em tópicos de sistemas (prefixo '1 -').",
            )
            return

        allowed = _allowed_roles(guild.id)
        member = ctx.author if isinstance(ctx.author, discord.Member) else None
        if member is None:
            try:
                member = await guild.fetch_member(ctx.author.id)
            except Exception:
                member = None

        if not _member_has_role(member, allowed):
            await ctx.reply(
                "Apenas membros autorizados (TI/ChatGuru/Whom/ClickUp) podem executar este comando.",
                mention_author=False,
            )
            return

        removed, failed = await _remove_non_allowed(channel, guild, allowed)
        summary = f"Removidos: {len(removed)}."
        if failed:
            summary += f" Falhas: {len(failed)} (veja logs)."
        await ctx.reply(summary, mention_author=False)

        if channel.id in PENDING_PAYLOADS:
            view = SectorSelectView(guild_id=guild.id, thread_id=channel.id)
            await channel.send(
                "Por favor, selecione o **Setor do colaborador** para encaminhar ao N8N:",
                view=view,
            )
        else:
            await ctx.reply(
                "Nenhum payload pendente para este tópico (já enviado?).",
                mention_author=False,
            )
