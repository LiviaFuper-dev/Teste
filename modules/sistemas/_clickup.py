"""
_clickup.py — Fluxo especial de lentidão do ClickUp.

Quando o usuário reporta "Lentidão/Travamento" no ClickUp,
a engine redireciona para cá em vez do diagnóstico genérico.
"""

import discord

import config
from ._engine import _disable_view, _ping_role, _update_step


class ClickupSlowView(discord.ui.View):
    """Opções após a dica de lentidão do ClickUp."""

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

    @discord.ui.button(label="Resolveu", style=discord.ButtonStyle.success)
    async def resolveu(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "clickup_lentidao", "resolveu")
        await _disable_view(interaction, self)
        await interaction.response.send_message(
            "Ótimo! Fico feliz que tenha resolvido. Se precisar, reabra o chamado.",
            ephemeral=True,
        )
        from ._engine import _finalizar_resolvido
        await _finalizar_resolvido(interaction, config.CLICKUP_SUPPORT_ROLE_ID)

    @discord.ui.button(label="Não resolveu", style=discord.ButtonStyle.danger)
    async def nao_resolveu(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "clickup_lentidao", "nao_resolveu")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await _ping_role(
            interaction.channel,
            interaction.guild,
            config.CLICKUP_SUPPORT_ROLE_ID,
            "🔧 O usuário tentou as dicas de lentidão mas o problema persiste. Equipe ClickUp, por favor verifiquem.",
        )
