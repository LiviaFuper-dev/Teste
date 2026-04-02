"""
_robos.py — Sistema Robôs/Automações.

Fluxo:
  Usuário clica "Robôs/Automações" → ephemeral RobosOpcaoView (some após escolha)
  → clica em INSS / ChatGuru / Planilhas / IA
  → modal abre pedindo descrição do problema (+ sugestão de print)
  → on_submit: thread criada + payload inicializado + fluxo de cada subsistema

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


# ── Modal de descrição ────────────────────────────────────────────────────────

class RobosDescricaoModal(discord.ui.Modal):
    """
    Aberto ao clicar em qualquer opção de Robôs/Automações.
    Coleta a descrição do problema antes de criar a thread.
    """

    descricao = discord.ui.TextInput(
        label="Descreva o problema",
        style=discord.TextStyle.paragraph,
        placeholder="Explique com detalhes o que está acontecendo...",
        required=True,
        max_length=1000,
    )

    def __init__(self, opcao: str, menu_interaction: discord.Interaction):
        super().__init__(title=f"Robôs/Automações — {opcao}")
        self.opcao = opcao
        self.menu_interaction = menu_interaction

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user
        descricao = self.descricao.value.strip()

        if not guild or not channel:
            await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
            return

        # ── Cria a thread ──────────────────────────────────────────────────────
        try:
            thread = await channel.create_thread(
                name=f"3 - Robôs/{self.opcao} - {user.display_name}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
            )
        except Exception as e:
            await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
            print(f"[ROBOS] Erro ao criar thread Robôs/{self.opcao}: {e}")
            return

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

        # ── Inicializa payload base ────────────────────────────────────────────
        PENDING_PAYLOADS[thread.id] = {
            "event":        "topic_created",
            "system":       "Automações",
            "subsystem":    self.opcao,
            "description":  descricao,
            "user_id":      user.id,
            "user_name":    user.display_name,
            "user_tag":     str(user),
            "guild_id":     guild.id,
            "guild_name":   guild.name,
            "channel_id":   channel.id,
            "channel_name": getattr(channel, "name", None),
            "thread_id":    thread.id,
            "thread_name":  thread.name,
            "thread_url":   getattr(thread, "jump_url", None),
            "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
            "steps":        {},
        }
        _update_step(thread.id, "descricao_inicial", descricao)
        print(f"[ROBOS] Payload inicializado: thread {thread.id} subsystem={self.opcao}")

        # ── Mensagem de boas-vindas + descrição registrada ────────────────────
        await thread.send(
            f"Olá, {user.mention}! Tudo bem? 😊\n\n"
            f"📋 **Problema relatado:**\n> {descricao}\n\n"
            "📸 Se tiver um **print da tela** com o erro ou o comportamento inesperado, "
            "por favor envie aqui — isso agiliza muito o diagnóstico!"
        )

        # ── Ramifica por subsistema ────────────────────────────────────────────
        if self.opcao == "INSS":
            await iniciar_fluxo_inss(thread, user)

        elif self.opcao == "ChatGuru":
            await iniciar_fluxo_chatguru(thread, user)

        elif self.opcao == "Planilhas":
            _update_step(thread.id, "opcao_escolhida", "planilhas")
            await _ping_role(
                thread, guild, _CARGO_TI_ID,
                "Nossa equipe foi acionada e entrará em contato em breve.",
            )

        elif self.opcao == "IA":
            _update_step(thread.id, "opcao_escolhida", "ia")
            await _ping_role(
                thread, guild, _CARGO_TI_ID,
                "Nossa equipe foi acionada e entrará em contato em breve.",
            )

        await interaction.followup.send("Tópico criado! Acesse-o para continuar.", ephemeral=True)

        # Fecha o ephemeral do ServicesView / RobosOpcaoView após 3s
        if self.menu_interaction:
            await asyncio.sleep(3)
            try:
                await self.menu_interaction.delete_original_response()
            except Exception:
                pass


# ── View de seleção ───────────────────────────────────────────────────────────

class RobosOpcaoView(discord.ui.View):
    def __init__(self, menu_interaction: discord.Interaction = None):
        super().__init__(timeout=None)
        self.menu_interaction = menu_interaction

    @discord.ui.button(label="INSS", style=discord.ButtonStyle.primary)
    async def inss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RobosDescricaoModal("INSS", self.menu_interaction)
        )

    @discord.ui.button(label="ChatGuru", style=discord.ButtonStyle.success)
    async def chatguru(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RobosDescricaoModal("ChatGuru", self.menu_interaction)
        )

    @discord.ui.button(label="Planilhas", style=discord.ButtonStyle.secondary)
    async def planilhas(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RobosDescricaoModal("Planilhas", self.menu_interaction)
        )

    @discord.ui.button(label="IA", style=discord.ButtonStyle.danger)
    async def ia(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            RobosDescricaoModal("IA", self.menu_interaction)
        )


# ── Entry point chamado pelo ServicesView ─────────────────────────────────────

async def _abrir_robos_menu(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "Selecione a área de **Robôs/Automações**:",
        view=RobosOpcaoView(menu_interaction=interaction),
        ephemeral=True,
    )