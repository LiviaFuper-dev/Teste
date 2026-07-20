"""
_whom.py — Passo final do fluxo Whom.

Contém:
  - WhomWarningView  (extensão com aviso em vermelho/amarelo?)
"""

import discord

from ._engine import _update_step, _disable_view, _ping_role, _finalizar_resolvido, role_id_for_system


class WhomWarningView(discord.ui.View):
    """Pergunta se a extensão do Whom está exibindo algum aviso."""

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

    @discord.ui.button(label="não funcionou", style=discord.ButtonStyle.danger)
    async def not_work(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "aviso_extensao_whom", "nao_funcionou")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await _ping_role(
            interaction.channel, interaction.guild, role_id_for_system(interaction.guild.id, "Whom"),
            "❗ O usuário informou que a extensão mostra aviso e as tentativas não funcionaram. Chamando equipe Whom:",
        )

    @discord.ui.button(label="resolveu", style=discord.ButtonStyle.success)
    async def solved(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "aviso_extensao_whom", "resolveu")
        await _disable_view(interaction, self)
        await interaction.response.send_message("Ótimo, que bom que resolveu!", ephemeral=True)
        await _finalizar_resolvido(interaction, role_id_for_system(interaction.guild.id, "Whom"))
