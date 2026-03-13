"""
_email.py — Suporte a problemas de e-mail.

Fluxo:
  Usuário clica "E-mail" no ServicesView → thread "3 - E-mail - {usuario}"
  → bot pergunta o domínio → @gmail.com ou @mlradvogados.com
  → pinga o cargo responsável
"""

import discord

import config
from ._engine import _disable_view, _ping_role


class EmailDomainView(discord.ui.View):
    """Pergunta qual é o domínio do e-mail do usuário."""

    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Apenas o solicitante pode interagir aqui.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="@gmail.com", style=discord.ButtonStyle.danger)
    async def gmail(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await _ping_role(
            interaction.channel, interaction.guild, config.EMAIL_GMAIL_ROLE_ID,
            "📧 O usuário informou que usa **@gmail.com**:",
        )

    @discord.ui.button(label="@mlradvogados.com", style=discord.ButtonStyle.primary)
    async def mlr(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await _ping_role(
            interaction.channel, interaction.guild, config.EMAIL_MLR_ROLE_ID,
            "📧 O usuário informou que usa **@mlradvogados.com**:",
        )


async def _criar_thread_email(interaction: discord.Interaction) -> None:
    """Cria a thread de e-mail e envia a pergunta de domínio."""
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    channel = interaction.channel
    user = interaction.user

    if not guild or not channel:
        await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
        return

    try:
        thread = await channel.create_thread(
            name=f"3 - E-mail - {user.display_name}",
            type=discord.ChannelType.private_thread,
            auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
        )
    except Exception as e:
        await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
        print(f"[SISTEMAS] Erro ao criar thread de e-mail: {e}")
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

    try:
        await thread.send(
            f"Olá {user.mention}! O final do seu e-mail é:",
            view=EmailDomainView(user.id),
        )
    except Exception as e:
        print(f"[SISTEMAS] Erro ao enviar mensagem na thread de e-mail: {e}")
