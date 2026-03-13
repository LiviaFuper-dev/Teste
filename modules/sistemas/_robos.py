"""
_robos.py — Sistema Robôs/Automações.

Fluxo geral:
  Usuário clica "Robôs/Automações" → ephemeral RobosOpcaoView (some após escolha)
  → INSS / ChatGuru / Planilhas / IA → thread "3 - Robôs/{opcao} - {usuario}"

Fluxo INSS:
  Q1: O bot não está baixando todos os documentos?
    → Não → pede print + pinga TI
    → Sim → Q2

  Q2: Isso aconteceu com apenas esse cliente, ou com vários?
    → Qualquer opção → Q3

  Q3: É sempre o mesmo documento ou aleatório?
    → Qualquer opção → pede print + envia resumo + pinga TI

Para adicionar diagnóstico a outra opção:
  Crie as views/funções aqui mesmo e chame no fluxo correspondente em _criar_thread().
"""

import asyncio

import discord

import config
from ._engine import _disable_view, _ping_role
from ._robos_chatguru import iniciar_fluxo_chatguru
from ._robo_inss import iniciar_fluxo_inss

_CARGO_TI_ID = 1415390806541598831

# Payloads pendentes por thread (thread_id → dict) — futuro envio ao N8N
ROBOS_PAYLOADS: dict[int, dict] = {}


def _init_payload(thread_id: int, user: discord.Member, opcao: str) -> None:
    ROBOS_PAYLOADS[thread_id] = {
        "thread_id": thread_id,
        "user_id": user.id,
        "user_name": user.display_name,
        "sistema": f"Robôs/{opcao}",
        "steps": {},
    }


def _step(thread_id: int, key: str, value: str) -> None:
    payload = ROBOS_PAYLOADS.get(thread_id)
    if payload:
        payload["steps"][key] = value


def _build_resumo_inss(thread_id: int) -> str:
    steps = ROBOS_PAYLOADS.get(thread_id, {}).get("steps", {})

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
        linhas.append(f"• **Abrangência:** {abrangencia_map.get(steps['abrangencia'], steps['abrangencia'])}")
    if "tipo_documento" in steps:
        linhas.append(f"• **Tipo:** {doc_map.get(steps['tipo_documento'], steps['tipo_documento'])}")

    return "\n".join(linhas)


async def _escalar_inss(thread: discord.Thread, guild: discord.Guild, thread_id: int) -> None:
    """Pede print, envia resumo e pinga o TI."""
    await thread.send(
        "📸 Por favor, envie aqui um **print da tela** com o erro ou a situação atual. "
        "Isso vai agilizar bastante a análise da equipe."
    )
    await thread.send(_build_resumo_inss(thread_id))
    await _ping_role(
        thread, guild, _CARGO_TI_ID,
        "🛠️ Equipe de T.I., há um chamado aguardando análise no robô do INSS:",
    )


# ── INSS Q3 — Tipo de documento ───────────────────────────────────────────────

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
        await _escalar_inss(interaction.channel, interaction.guild, interaction.channel.id)

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


# ── INSS Q2 — Abrangência ─────────────────────────────────────────────────────

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
        _step(interaction.channel.id, "abrangencia", valor)
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
        await self._avancar_q3(interaction, "todos")


# ── INSS Q1 — Problema com download? ─────────────────────────────────────────

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
            "**print da tela** com o problema? Isso vai ajudar bastante na análise. 📸"
        )
        await _ping_role(
            interaction.channel, interaction.guild, _CARGO_TI_ID,
            "🛠️ Equipe de T.I., há um chamado aguardando análise no robô do INSS:",
        )


# ── RobosOpcaoView ────────────────────────────────────────────────────────────

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

    async def _criar_thread(self, interaction: discord.Interaction, opcao: str, fluxo=None) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user

        if not guild or not channel:
            await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
            return

        try:
            thread = await channel.create_thread(
                name=f"3 - Robôs/{opcao} - {user.display_name}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
            )
        except Exception as e:
            await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
            print(f"[SISTEMAS] Erro ao criar thread Robôs/{opcao}: {e}")
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

        # Inicia o fluxo específico de cada opção
        if fluxo:
            _init_payload(thread.id, user, opcao)
            await fluxo(thread, user)
        else:
            # Fluxo a definir futuramente
            await thread.send(
                f"Olá {user.mention}! 🤖 Chamado aberto para **Robôs/Automações — {opcao}**.\n"
                "Descreva o problema que está enfrentando e aguarde o atendimento."
            )

        await self._fechar_este_ephemeral()

    @discord.ui.button(label="INSS", style=discord.ButtonStyle.primary)
    async def inss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._criar_thread(interaction, "INSS", fluxo=iniciar_fluxo_inss)

    @discord.ui.button(label="ChatGuru", style=discord.ButtonStyle.success)
    async def chatguru(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._criar_thread(interaction, "ChatGuru", fluxo=iniciar_fluxo_chatguru)

    @discord.ui.button(label="Planilhas", style=discord.ButtonStyle.secondary)
    async def planilhas(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._criar_thread(interaction, "Planilhas")

    @discord.ui.button(label="IA", style=discord.ButtonStyle.danger)
    async def ia(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._criar_thread(interaction, "IA")


async def _abrir_robos_menu(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "Selecione a área de **Robôs/Automações**:",
        view=RobosOpcaoView(menu_interaction=interaction),
        ephemeral=True,
    )