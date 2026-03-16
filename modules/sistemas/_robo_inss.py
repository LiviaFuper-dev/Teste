"""
_inss.py — Fluxo de diagnóstico para o robô do INSS.

Fluxo:
  Q1: "O bot não está baixando todos os documentos?"
    → Não  → pede print + pinga TI imediatamente
    → Sim  → Q2

  Q2: "Isso aconteceu com apenas esse cliente ou com vários?"
    → Apenas esse / Vários → Q3
    → Todos os clientes    → escala imediatamente (sem Q3)

  Q3: "É sempre o mesmo documento ou aleatório?"
    → qualquer resposta → pede print + envia resumo + pinga TI

Todos os steps são registrados em PENDING_PAYLOADS (inicializado em _robos.py).
Enviado ao N8N quando o TI executa !sistema.

Estrutura dos steps no payload:
  {
    "documentos_faltando": "sim" | "nao",
    "abrangencia": "apenas_esse" | "varios" | "todos",
    "tipo_documento": "sempre_mesmo" | "aleatorios" | "nao_sei",
  }
"""

import discord

from ._engine import PENDING_PAYLOADS, _disable_view, _ping_role, _update_step

_CARGO_TI_ID = 1415390806541598831


def _build_resumo(thread_id: int) -> str:
    """Monta o resumo das escolhas para exibir no tópico quando o TI chegar."""
    payload = PENDING_PAYLOADS.get(thread_id, {})
    steps = payload.get("steps", {})

    abrangencia_map = {
        "apenas_esse": "Apenas um cliente",
        "varios":      "Vários clientes",
        "todos":       "Todos os clientes",
    }
    doc_map = {
        "sempre_mesmo": "Sempre o mesmo documento",
        "aleatorios":   "Documentos aleatórios",
        "nao_sei":      "Não sabe ao certo",
    }

    linhas = ["📋 **Resumo do chamado — Robô INSS**\n"]

    if "documentos_faltando" in steps:
        val = "✅ Sim" if steps["documentos_faltando"] == "sim" else "❌ Não"
        linhas.append(f"• **Bot deixando de baixar documentos?** {val}")

    if "abrangencia" in steps:
        linhas.append(
            f"• **Abrangência:** {abrangencia_map.get(steps['abrangencia'], steps['abrangencia'])}"
        )

    if "tipo_documento" in steps:
        linhas.append(
            f"• **Tipo:** {doc_map.get(steps['tipo_documento'], steps['tipo_documento'])}"
        )

    return "\n".join(linhas)


async def _escalar(
    thread: discord.Thread,
    guild: discord.Guild,
    thread_id: int,
) -> None:
    """Pede print, exibe resumo das escolhas e pinga o TI."""
    await thread.send(
        "📸 Por favor, envie aqui um **print da tela** com o erro ou a situação atual. "
        "Isso vai ajudar a equipe a identificar o problema com mais rapidez."
    )
    await thread.send(_build_resumo(thread_id))
    await _ping_role(
        thread, guild, _CARGO_TI_ID,
        "🛠️ Equipe de T.I., há um chamado aguardando análise no robô do INSS:",
    )


# ── Q3 — Tipo de documento ────────────────────────────────────────────────────

class InssQ3View(discord.ui.View):
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
        _update_step(interaction.channel.id, "tipo_documento", valor)
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await _escalar(interaction.channel, interaction.guild, interaction.channel.id)

    @discord.ui.button(label="Sempre o mesmo documento", style=discord.ButtonStyle.danger)
    async def mesmo(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await self._responder(interaction, "sempre_mesmo")

    @discord.ui.button(label="Documentos aleatórios", style=discord.ButtonStyle.primary)
    async def aleatorio(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await self._responder(interaction, "aleatorios")

    @discord.ui.button(label="Não sei ao certo", style=discord.ButtonStyle.secondary)
    async def nao_sei(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await self._responder(interaction, "nao_sei")


# ── Q2 — Abrangência ─────────────────────────────────────────────────────────

class InssQ2View(discord.ui.View):
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

    async def _avancar_q3(self, interaction: discord.Interaction, valor: str) -> None:
        _update_step(interaction.channel.id, "abrangencia", valor)
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await interaction.channel.send(
            "Entendido. Agora me diz: é sempre o mesmo documento que não está sendo baixado, "
            "ou os documentos que faltam variam?",
            view=InssQ3View(self.original_user_id),
        )

    @discord.ui.button(label="Apenas esse cliente", style=discord.ButtonStyle.success)
    async def apenas_esse(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await self._avancar_q3(interaction, "apenas_esse")

    @discord.ui.button(label="Vários clientes", style=discord.ButtonStyle.primary)
    async def varios(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await self._avancar_q3(interaction, "varios")

    @discord.ui.button(label="Todos os clientes", style=discord.ButtonStyle.danger)
    async def todos(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "abrangencia", "todos")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await interaction.channel.send(
            "Entendido, o problema está afetando **todos os clientes**. "
            "Já estou acionando a equipe."
        )
        await _escalar(interaction.channel, interaction.guild, interaction.channel.id)


# ── Q1 — Problema com download de documentos? ────────────────────────────────

class InssQ1View(discord.ui.View):
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

    @discord.ui.button(label="Sim", style=discord.ButtonStyle.success)
    async def sim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "documentos_faltando", "sim")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await interaction.channel.send(
            "Isso aconteceu com apenas esse cliente, ou você está vendo o problema em vários?",
            view=InssQ2View(self.original_user_id),
        )

    @discord.ui.button(label="Não", style=discord.ButtonStyle.danger)
    async def nao(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "documentos_faltando", "nao")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await interaction.channel.send(
            "Entendido. Pode descrever melhor o que está acontecendo e enviar um "
            "**print da tela** com o problema? Isso vai ajudar bastante na análise. 📸"
        )
        await _ping_role(
            interaction.channel, interaction.guild, _CARGO_TI_ID,
            "🛠️ Equipe de T.I., há um chamado aguardando análise no robô do INSS:",
        )


# ── Entrada do fluxo ──────────────────────────────────────────────────────────

async def iniciar_fluxo_inss(thread: discord.Thread, user: discord.Member) -> None:
    """
    Chamada pelo _robos.py após criar a thread e inicializar o PENDING_PAYLOADS.
    Inicia o diagnóstico com a Q1.
    """
    await thread.send(
        f"Olá, {user.mention}! 👋 Vou te ajudar a identificar o problema com o robô do INSS.\n\n"
        "O problema é que o bot **não está baixando todos os documentos**?",
        view=InssQ1View(user.id),
    )