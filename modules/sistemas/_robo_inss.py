"""
_inss.py — Fluxo de diagnóstico para o robô do INSS.

Fluxo:
  Q1: "O bot não está baixando todos os documentos?"
    → Não  → pede print + pinga TI imediatamente
    → Sim  → Q2

  Q2: "Isso aconteceu com apenas esse cliente ou com vários?"
    → Todos os clientes → escala com prioridade ALTA imediatamente (sem Q3)
    → Apenas esse / Vários → Q3

  Q3: "É sempre o mesmo documento ou aleatório?"
    → qualquer resposta → pede print + pinga TI + envia resumo das escolhas

Estrutura do payload (salvo em INSS_PAYLOADS, futuro envio ao N8N):
  {
    "thread_id": ...,
    "user_id": ...,
    "sistema": "INSS",
    "steps": {
      "documentos_faltando": "sim" | "nao",
      "abrangencia": "apenas_esse" | "varios" | "todos",
      "tipo_documento": "sempre_mesmo" | "aleatorios" | "nao_sei",
    },
    "prioridade": "normal" | "alta",
  }
"""

import asyncio

import discord

import config
from ._engine import _disable_view, _ping_role

_CARGO_TI_ID = 1415390806541598831

# Payloads pendentes por thread (thread_id → dict)
INSS_PAYLOADS: dict[int, dict] = {}


def _init_payload(thread_id: int, user: discord.User | discord.Member) -> None:
    INSS_PAYLOADS[thread_id] = {
        "thread_id": thread_id,
        "user_id": user.id,
        "user_name": user.display_name,
        "sistema": "INSS",
        "steps": {},
        "prioridade": "normal",
    }


def _step(thread_id: int, key: str, value: str) -> None:
    payload = INSS_PAYLOADS.get(thread_id)
    if payload:
        payload["steps"][key] = value


def _build_resumo(thread_id: int) -> str:
    """Monta o resumo das escolhas para o TI."""
    payload = INSS_PAYLOADS.get(thread_id, {})
    steps = payload.get("steps", {})
    prioridade = payload.get("prioridade", "normal")
    emoji_prioridade = "🔴" if prioridade == "alta" else "🟡"

    linhas = [f"📋 **Resumo do chamado — Robô INSS** {emoji_prioridade} `{prioridade.upper()}`\n"]

    abrangencia_map = {
        "apenas_esse": "Apenas um cliente",
        "varios": "Vários clientes",
        "todos": "Todos os clientes",
    }
    doc_map = {
        "sempre_mesmo": "Sempre o mesmo documento",
        "aleatorios": "Documentos aleatórios",
        "nao_sei": "Não sabe ao certo",
    }

    if "documentos_faltando" in steps:
        val = "✅ Sim" if steps["documentos_faltando"] == "sim" else "❌ Não"
        linhas.append(f"• **Bot deixando de baixar documentos?** {val}")

    if "abrangencia" in steps:
        linhas.append(f"• **Abrangência:** {abrangencia_map.get(steps['abrangencia'], steps['abrangencia'])}")

    if "tipo_documento" in steps:
        linhas.append(f"• **Tipo:** {doc_map.get(steps['tipo_documento'], steps['tipo_documento'])}")

    return "\n".join(linhas)


async def _escalar(
    thread: discord.Thread,
    guild: discord.Guild,
    thread_id: int,
    prioridade: str = "normal",
) -> None:
    """Pede print, envia resumo e pinga o TI."""
    INSS_PAYLOADS.get(thread_id, {})["prioridade"] = prioridade
    resumo = _build_resumo(thread_id)

    await thread.send(
        "📸 Por favor, envie aqui um **print da tela** com o erro ou a situação atual. "
        "Isso vai ajudar a equipe a identificar o problema com mais rapidez."
    )
    await thread.send(resumo)
    await _ping_role(
        thread, guild, _CARGO_TI_ID,
        "🛠️ Chamando a equipe de T.I. para te ajudar!",
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
        _step(interaction.channel.id, "tipo_documento", valor)
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

    @discord.ui.button(label="Apenas esse cliente", style=discord.ButtonStyle.success)
    async def apenas_esse(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _step(interaction.channel.id, "abrangencia", "apenas_esse")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await interaction.channel.send(
            "Entendido. Agora me diz: é sempre o mesmo documento que não está sendo baixado, "
            "ou os documentos que faltam variam?",
            view=InssQ3View(self.original_user_id),
        )

    @discord.ui.button(label="Vários clientes", style=discord.ButtonStyle.primary)
    async def varios(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _step(interaction.channel.id, "abrangencia", "varios")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await interaction.channel.send(
            "Ok. Agora me diz: é sempre o mesmo documento que não está sendo baixado, "
            "ou os documentos que faltam variam?",
            view=InssQ3View(self.original_user_id),
        )

    @discord.ui.button(label="Todos os clientes", style=discord.ButtonStyle.danger)
    async def todos(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _step(interaction.channel.id, "abrangencia", "todos")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        # Impacto total → escala imediatamente com prioridade alta, sem Q3
        await interaction.channel.send(
            "⚠️ Entendido — o problema está afetando **todos os clientes**. "
            "Escalando com prioridade alta."
        )
        await _escalar(
            interaction.channel, interaction.guild,
            interaction.channel.id, prioridade="alta",
        )


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
        _step(interaction.channel.id, "documentos_faltando", "sim")
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
        _step(interaction.channel.id, "documentos_faltando", "nao")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await interaction.channel.send(
            "Entendido. Pode descrever melhor o que está acontecendo e enviar um "
            "**print da tela** com o problema? Isso vai ajudar muito na análise. 📸"
        )
        await _ping_role(
            interaction.channel, interaction.guild, _CARGO_TI_ID,
            "🛠️ Equipe de T.I., há um chamado aguardando análise no robô do INSS:",
        )


# ── Entrada do fluxo INSS ─────────────────────────────────────────────────────

async def iniciar_fluxo_inss(thread: discord.Thread, user: discord.Member) -> None:
    """Chamada logo após a criação da thread de INSS. Inicia o diagnóstico."""
    _init_payload(thread.id, user)
    await thread.send(
        f"Olá, {user.mention}! 👋 Vou te ajudar a identificar o problema com o robô do INSS.\n\n"
        "O problema é que o bot **não está baixando todos os documentos**?",
        view=InssQ1View(user.id),
    )