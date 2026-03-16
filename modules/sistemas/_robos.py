"""
_robos.py — Sistema Robôs/Automações.

Fluxo:
  Usuário clica "Robôs/Automações" → ephemeral RobosOpcaoView (some após escolha)
  → INSS      → thread + payload inicializado + fluxo de diagnóstico (_inss.py)
  → ChatGuru  → thread + payload inicializado + fluxo de diagnóstico (_robos_chatguru.py)
  → Planilhas → thread + payload inicializado + ping direto (sem steps)
  → IA        → thread + payload inicializado + ping direto (sem steps)

O payload base (system: "Automações", subsystem: opcao) é inicializado aqui.
Cada fluxo filho adiciona seus steps via _update_step().
Enviado ao N8N quando o TI executa !sistema.
"""

import asyncio
import datetime

import discord

import config
from ._engine import PENDING_PAYLOADS, _ping_role, _update_step
from ._robo_inss import iniciar_fluxo_inss
from ._robos_chatguru import iniciar_fluxo_chatguru

_CARGO_TI_ID = 1415390806541598831


class RobosOpcaoView(discord.ui.View):
    def __init__(self, menu_interaction: discord.Interaction = None):
        super().__init__(timeout=None)
        self.menu_interaction = menu_interaction

    async def _fechar_este_ephemeral(self) -> None:
        if self.menu_interaction:
            await asyncio.sleep(3)
            try:
                await self.menu_interaction.delete_original_response()
            except Exception:
                pass

    async def _criar_thread(self, interaction: discord.Interaction, opcao: str) -> discord.Thread | None:
        """Cria a thread e inicializa o payload base comum a todos os robôs."""
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user

        if not guild or not channel:
            await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
            return None

        try:
            thread = await channel.create_thread(
                name=f"3 - Robôs/{opcao} - {user.display_name}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
            )
        except Exception as e:
            await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
            print(f"[ROBOS] Erro ao criar thread Robôs/{opcao}: {e}")
            return None

        try:
            await thread.join()
        except Exception:
            try:
                await thread.add_user(interaction.client.user)
            except Exception:
                pass

        try:
            await thread.add_user(user)
        except Exception:
            pass

        # Payload base — steps serão preenchidos pelo fluxo de cada subsistema
        PENDING_PAYLOADS[thread.id] = {
            "event": "topic_created",
            "system": "Automações",
            "subsystem": opcao,
            "user_id": user.id,
            "user_name": user.display_name,
            "user_tag": str(user),
            "guild_id": guild.id,
            "guild_name": guild.name,
            "channel_id": channel.id,
            "channel_name": getattr(channel, "name", None),
            "thread_id": thread.id,
            "thread_name": thread.name,
            "thread_url": getattr(thread, "jump_url", None),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "steps": {},
        }
        print(f"[ROBOS] Payload inicializado: thread {thread.id} subsystem={opcao}")

        await interaction.followup.send("Tópico criado! Acesse-o para continuar.", ephemeral=True)
        return thread

    # ── INSS ──────────────────────────────────────────────────────────────────

    @discord.ui.button(label="INSS", style=discord.ButtonStyle.primary)
    async def inss(self, interaction: discord.Interaction, button: discord.ui.Button):
        thread = await self._criar_thread(interaction, "INSS")
        if thread:
            await iniciar_fluxo_inss(thread, interaction.user)
            await self._fechar_este_ephemeral()

    # ── ChatGuru ──────────────────────────────────────────────────────────────

    @discord.ui.button(label="ChatGuru", style=discord.ButtonStyle.success)
    async def chatguru(self, interaction: discord.Interaction, button: discord.ui.Button):
        thread = await self._criar_thread(interaction, "ChatGuru")
        if thread:
            await iniciar_fluxo_chatguru(thread, interaction.user)
            await self._fechar_este_ephemeral()

    # ── Planilhas — ping direto, sem steps ───────────────────────────────────

    @discord.ui.button(label="Planilhas", style=discord.ButtonStyle.secondary)
    async def planilhas(self, interaction: discord.Interaction, button: discord.ui.Button):
        thread = await self._criar_thread(interaction, "Planilhas")
        if thread:
            _update_step(thread.id, "opcao_escolhida", "planilhas")
            await _ping_role(
                thread, interaction.guild, _CARGO_TI_ID,
                f"Olá, {interaction.user.mention}! Tudo bem? 😊\n\n"
                "Recebemos seu chamado sobre **Robôs/Automações — Planilhas**.\n"
                "Por favor, descreva aqui o que está acontecendo com o máximo de detalhes possível "
                "e nossa equipe entrará em contato em breve.",
            )
            await self._fechar_este_ephemeral()

    # ── IA — ping direto, sem steps ───────────────────────────────────────────

    @discord.ui.button(label="IA", style=discord.ButtonStyle.danger)
    async def ia(self, interaction: discord.Interaction, button: discord.ui.Button):
        thread = await self._criar_thread(interaction, "IA")
        if thread:
            _update_step(thread.id, "opcao_escolhida", "ia")
            await _ping_role(
                thread, interaction.guild, _CARGO_TI_ID,
                f"Olá, {interaction.user.mention}! Tudo bem? 😊\n\n"
                "Recebemos seu chamado sobre **Robôs/Automações — IA**.\n"
                "Por favor, descreva aqui o que está acontecendo com o máximo de detalhes possível "
                "e nossa equipe entrará em contato em breve.",
            )
            await self._fechar_este_ephemeral()


async def _abrir_robos_menu(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "Selecione a área de **Robôs/Automações**:",
        view=RobosOpcaoView(menu_interaction=interaction),
        ephemeral=True,
    )