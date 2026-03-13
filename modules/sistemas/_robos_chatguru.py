"""
_robos_chatguru.py — Fluxo de diagnóstico para o robô do ChatGuru.

Fluxo:
  Q1: O robô não está baixando todas as imagens?
    → Está baixando algumas → resumo + pinga TI
    → Baixando nenhuma     → resumo + pinga TI
"""

import discord

from ._engine import _disable_view, _ping_role

_CARGO_TI_ID = 1415390806541598831

CHATGURU_ROBOS_PAYLOADS: dict[int, dict] = {}


def _init_payload(thread_id: int, user: discord.Member) -> None:
    CHATGURU_ROBOS_PAYLOADS[thread_id] = {
        "thread_id": thread_id,
        "user_id": user.id,
        "user_name": user.display_name,
        "sistema": "Robôs/ChatGuru",
        "steps": {},
    }


def _step(thread_id: int, key: str, value: str) -> None:
    payload = CHATGURU_ROBOS_PAYLOADS.get(thread_id)
    if payload:
        payload["steps"][key] = value


def _build_resumo(thread_id: int) -> str:
    steps = CHATGURU_ROBOS_PAYLOADS.get(thread_id, {}).get("steps", {})

    imagens_map = {
        "algumas": "Está baixando algumas imagens",
        "nenhuma": "Não está baixando nenhuma imagem",
    }

    linhas = ["📋 **Resumo do chamado — Robô ChatGuru**\n"]
    if "imagens" in steps:
        linhas.append(f"• **Status do download:** {imagens_map.get(steps['imagens'], steps['imagens'])}")

    return "\n".join(linhas)


async def _escalar(thread: discord.Thread, guild: discord.Guild, thread_id: int) -> None:
    await thread.send(
        "📸 Por favor, envie aqui um **print da tela** com o erro ou a situação atual. "
        "Isso vai agilizar bastante a análise da equipe."
    )
    await thread.send(_build_resumo(thread_id))
    await _ping_role(
        thread, guild, _CARGO_TI_ID,
        "🛠️ Equipe de T.I., há um chamado aguardando análise no robô do ChatGuru:",
    )


# ── Q1 — Download de imagens ──────────────────────────────────────────────────

class ChatGuruRobosQ1View(discord.ui.View):
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

    async def _responder(self, interaction: discord.Interaction, valor: str) -> None:
        _step(interaction.channel.id, "imagens", valor)
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await _escalar(interaction.channel, interaction.guild, interaction.channel.id)

    @discord.ui.button(label="Está baixando algumas", style=discord.ButtonStyle.primary)
    async def algumas(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await self._responder(interaction, "algumas")

    @discord.ui.button(label="Baixando nenhuma", style=discord.ButtonStyle.danger)
    async def nenhuma(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await self._responder(interaction, "nenhuma")


# ── Entrada do fluxo ──────────────────────────────────────────────────────────

async def iniciar_fluxo_chatguru(thread: discord.Thread, user: discord.Member) -> None:
    _init_payload(thread.id, user)
    await thread.send(
        f"Olá, {user.mention}! 👋 Vou te ajudar a identificar o problema com o robô do ChatGuru.\n\n"
        "O problema que você está tendo é o robô **não está baixando todas as imagens**?",
        view=ChatGuruRobosQ1View(user.id),
    )