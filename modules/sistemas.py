"""
modules/sistemas.py — Módulo de Suporte a Sistemas (ChatGuru, Whom, ClickUp).

Fluxo:
  1. Usuário clica "3 - Sistemas ⚙️" no menu → ephemeral com ServicesView (3 botões)
  2. Clica no sistema → ServiceModal (descrição do problema)
  3. Modal cria thread "3 - {sistema} - {usuario}" e envia ProblemTypeView
  4. ProblemTypeView → DiagnosticoView (engine genérica: cache → reiniciar → aguardar)
  5. Se não resolveu até o fim → escalada para o cargo responsável
     (ChatGuru: ErrorNumberModal | Whom: WhomWarningView | ClickUp: ping direto)
  6. !sistema (só autorizados, dentro de thread "3 -") → remove não-autorizados
     → SectorSelectView → envia N8N → deleta thread

Comando: !sistema
"""

import asyncio
import datetime
import json
import os
import re
from typing import Optional

import discord
from discord.ext import commands
import requests

import config

# ── solutions.json ───────────────────────────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SOLUTIONS_FILE = os.path.join(os.path.dirname(_BASE_DIR), "solutions.json")

try:
    with open(_SOLUTIONS_FILE, "r", encoding="utf-8") as _f:
        ERROR_DB: dict[str, str] = {str(k): v for k, v in json.load(_f).items()}
    print(f"[SISTEMAS] solutions.json carregado — {len(ERROR_DB)} entradas.")
except FileNotFoundError:
    ERROR_DB = {}
    print("[SISTEMAS] solutions.json não encontrado. ERROR_DB vazio.")
except Exception as _e:
    ERROR_DB = {}
    print(f"[SISTEMAS] Erro ao carregar solutions.json: {_e}")

# ── Payloads pendentes (thread_id → payload em construção) ───────────────────
PENDING_PAYLOADS: dict[int, dict] = {}

# ── Config por sistema ───────────────────────────────────────────────────────
# steps     → passos genéricos (cache → reiniciar → aguardar 5min)
# final     → o que fazer quando todos os steps falharem:
#               "escalate"     → pinga role diretamente (ClickUp)
#               "error_modal"  → abre ErrorNumberModal (ChatGuru)
#               "whom_warning" → pergunta sobre extensão (Whom)
# lentidao_especial → True = lentidão/travamento tem texto técnico + botão único (ClickUp)

_STEPS_PADRAO = [
    {
        "step_key": "cache_limpo",
        "pergunta_chatguru": (
            "Olá tudo bem? Vi que você está tendo problema no ChatGuru, tô aqui "
            "pra ajudar. Deixa eu perguntar: você já limpou o cache? "
            "(tente limpar caso não tenha feito ainda)"
        ),
        "pergunta_padrao": (
            "Você já tentou limpar o cache da página? Limpando o cachê o site pode "
            "voltar ao normal, pois é um reset de dados."
        ),
    },
    {
        "step_key": "reinicio_pagina",
        "pergunta_padrao": (
            "🤔 Já tentou reiniciar a página? (Ctrl + F5 ou atualizar com o botão "
            "no canto superior esquerdo) (isso atualiza a página)"
        ),
    },
    {
        "step_key": "aguardou_5min",
        "pergunta_chatguru": (
            "⏳ Às vezes o problema é interno do sistema, já está esperando há 5 minutos "
            "e não resolveu?"
        ),
        "pergunta_padrao": (
            "⏳ Às vezes o problema é interno do sistema, já está esperando há cerca de "
            "5 minutos e não resolveu?"
        ),
    },
]

SISTEMAS_CONFIG: dict[str, dict] = {
    "ChatGuru": {
        "role_id": config.CHATGURU_ROLE_ID,
        "steps": _STEPS_PADRAO,
        "final": "error_modal",
        "lentidao_especial": False,
    },
    "Whom": {
        "role_id": config.WHOM_ROLE_ID,
        "steps": _STEPS_PADRAO,
        "final": "whom_warning",
        "lentidao_especial": False,
    },
    "Clickup": {
        "role_id": config.CLICKUP_SUPPORT_ROLE_ID,
        "steps": _STEPS_PADRAO,
        "final": "escalate",
        "lentidao_especial": True,
    },
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _update_step(thread_id: int, key: str, value: str) -> None:
    payload = PENDING_PAYLOADS.get(thread_id)
    if payload is not None:
        payload.setdefault("steps", {})[key] = value


def _get_pergunta(sistema: str, step: dict) -> str:
    if sistema == "ChatGuru" and "pergunta_chatguru" in step:
        return step["pergunta_chatguru"]
    return step["pergunta_padrao"]


async def _ping_role(
    thread: discord.Thread,
    guild: discord.Guild,
    role_id: int,
    msg: str,
) -> None:
    role = guild.get_role(role_id) if guild else None
    try:
        await thread.send(msg, allowed_mentions=discord.AllowedMentions(roles=True))
        if role:
            await thread.send(role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
        else:
            await thread.send(f"<@&{role_id}>", allowed_mentions=discord.AllowedMentions(roles=True))
    except Exception as e:
        print(f"[SISTEMAS] Erro ao pingar role {role_id}: {e}")


async def _disable_view(interaction: discord.Interaction, view: discord.ui.View) -> None:
    for item in view.children:
        try:
            item.disabled = True
        except Exception:
            pass
    try:
        if interaction.message:
            await interaction.message.edit(view=view)
    except Exception:
        pass


async def _finalizar_resolvido(interaction: discord.Interaction, role_id: int) -> None:
    thread = interaction.channel
    guild = interaction.guild
    try:
        await thread.remove_user(interaction.user)
    except Exception as e:
        print(f"[SISTEMAS] Não foi possível remover usuário da thread: {e}")
    role = guild.get_role(role_id) if guild else None
    try:
        if role:
            await thread.send(
                f"✅ Chamado resolvido pelo usuário. {role.mention}",
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        else:
            await thread.send(f"✅ Chamado resolvido pelo usuário. (cargo <@&{role_id}> não encontrado)")
    except Exception as e:
        print(f"[SISTEMAS] Erro ao pingar cargo após resolução: {e}")


async def _send_to_n8n(payload: dict) -> bool:
    url = config.N8N_WEBHOOK_SISTEMAS
    if not url:
        print("[SISTEMAS] N8N_WEBHOOK_SISTEMAS não configurado.")
        return False
    try:
        def _post():
            r = requests.post(url, json=payload, timeout=10)
            r.raise_for_status()
            return r
        resp = await asyncio.to_thread(_post)
        print(f"[SISTEMAS] Enviado para N8N, status: {resp.status_code}")
        return True
    except Exception as e:
        print(f"[SISTEMAS] Falha ao enviar para N8N: {e}")
        return False


def _allowed_roles(guild_id: int) -> set[int]:
    cfg = config.SERVIDORES.get(guild_id, {}).get("sistemas", {})
    roles = {config.CHATGURU_ROLE_ID, config.WHOM_ROLE_ID, config.CLICKUP_SUPPORT_ROLE_ID}
    cargo_ti = cfg.get("cargo_ti")
    if cargo_ti:
        roles.add(int(cargo_ti))
    return roles


def _member_has_role(member: Optional[discord.Member], role_ids: set[int]) -> bool:
    if member is None:
        return False
    return any(r.id in role_ids for r in member.roles)


# ── Engine genérica de diagnóstico ───────────────────────────────────────────

class DiagnosticoView(discord.ui.View):
    """
    View reutilizável para os passos: cache → reiniciar → aguardar 5min.
    Ao esgotar os steps, chama _escalada_final() com a lógica específica do sistema.
    """

    def __init__(self, sistema: str, step_index: int, original_user_id: int):
        super().__init__(timeout=None)
        self.sistema = sistema
        self.step_index = step_index
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Apenas o solicitante pode interagir aqui.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Não resolveu", style=discord.ButtonStyle.danger)
    async def no_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return

        cfg = SISTEMAS_CONFIG[self.sistema]
        steps = cfg["steps"]
        _update_step(interaction.channel.id, steps[self.step_index]["step_key"], "nao_resolveu")

        await _disable_view(interaction, self)
        next_index = self.step_index + 1

        if next_index < len(steps):
            await interaction.response.defer()
            pergunta = _get_pergunta(self.sistema, steps[next_index])
            await interaction.channel.send(
                pergunta,
                view=DiagnosticoView(self.sistema, next_index, self.original_user_id),
            )
        else:
            await interaction.response.defer()
            await _escalada_final(interaction, self.sistema, cfg)

    @discord.ui.button(label="Resolveu", style=discord.ButtonStyle.success)
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return

        cfg = SISTEMAS_CONFIG[self.sistema]
        _update_step(
            interaction.channel.id,
            cfg["steps"][self.step_index]["step_key"],
            "resolveu",
        )
        await _disable_view(interaction, self)

        msgs = {
            0: "Ótimo! Fico feliz que tenha resolvido. Se precisar, reabra o chamado.",
            1: "Perfeito, que bom que resolveu! 👍",
            2: "Ótimo, que bom que resolveu!",
        }
        await interaction.response.send_message(
            msgs.get(self.step_index, "Ótimo, problema resolvido!"), ephemeral=True
        )
        await _finalizar_resolvido(interaction, cfg["role_id"])


async def _escalada_final(
    interaction: discord.Interaction, sistema: str, cfg: dict
) -> None:
    """Chamada quando todos os passos genéricos falharam."""
    thread = interaction.channel
    guild = interaction.guild
    final = cfg["final"]

    if final == "error_modal":
        await thread.send(
            "🔍 Confirma aqui para mim: existe uma mensagem de erro na página atual? "
            "Passe o mouse por cima do ponto de exclamação e me diga qual o número aparece "
            "logo antes da mensagem (ex: '131049 - mensagem...')",
            view=ChatGuruFourthView(interaction.user.id),
        )

    elif final == "whom_warning":
        await thread.send(
            "🚨 A extensão do Whom apresenta algum aviso em vermelho ou amarelo?"
            " Tente clicar no botão 'status' (fica lá em baixo na extensão)"
            " Normalmente são ajustes internos e o tribunal volta. Isso aparece na extensão do navegador?",
            view=WhomWarningView(interaction.user.id),
        )

    elif final == "escalate":
        await _ping_role(
            thread, guild, cfg["role_id"],
            "❗ O usuário informou que tentou as tentativas sugeridas e não funcionou. Chamando equipe ClickUp:",
        )


# ── ChatGuru: passo final — existe mensagem de erro? ─────────────────────────

class ChatGuruFourthView(discord.ui.View):
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

    @discord.ui.button(label="não existe", style=discord.ButtonStyle.danger)
    async def no_exist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "mensagem_erro_existe", "nao")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await _ping_role(
            interaction.channel, interaction.guild, config.CHATGURU_ROLE_ID,
            "❗ O usuário informou que não há mensagem de erro visível. Chamando equipe ChatGuru:",
        )

    @discord.ui.button(label="existe", style=discord.ButtonStyle.primary)
    async def exist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "mensagem_erro_existe", "sim")
        await _disable_view(interaction, self)
        await interaction.response.send_modal(ErrorNumberModal(self.original_user_id))


# ── Whom: passo final — extensão com aviso? ───────────────────────────────────

class WhomWarningView(discord.ui.View):
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
            interaction.channel, interaction.guild, config.WHOM_ROLE_ID,
            "❗ O usuário informou que a extensão mostra aviso e as tentativas não funcionaram. Chamando equipe Whom:",
        )

    @discord.ui.button(label="resolveu", style=discord.ButtonStyle.success)
    async def solved(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "aviso_extensao_whom", "resolveu")
        await _disable_view(interaction, self)
        await interaction.response.send_message("Ótimo, que bom que resolveu!", ephemeral=True)
        await _finalizar_resolvido(interaction, config.WHOM_ROLE_ID)


# ── ClickUp: lentidão especial ────────────────────────────────────────────────

class ClickupSlowView(discord.ui.View):
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

    @discord.ui.button(label="Preciso muito de ajuda", style=discord.ButtonStyle.danger)
    async def urgent_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "solicitou_suporte_urgente_clickup", "sim")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await _ping_role(
            interaction.channel, interaction.guild, config.CLICKUP_SUPPORT_ROLE_ID,
            "❗ Usuário solicitou suporte ClickUp (precisa muito de ajuda). Chamando equipe ClickUp:",
        )


# ── Modal de número de erro (ChatGuru) ────────────────────────────────────────

class ErrorNumberModal(discord.ui.Modal, title="Número do erro"):
    error_number = discord.ui.TextInput(
        label="Informe o número do erro",
        placeholder="Ex: 131049 - mensagem...",
        style=discord.TextStyle.short,
        required=True,
        max_length=200,
    )

    def __init__(self, original_user_id: int):
        super().__init__()
        self.original_user_id = original_user_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Apenas o solicitante pode enviar este dado.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        thread = interaction.channel
        guild = interaction.guild
        user_input = self.error_number.value.strip()

        m = re.match(r'^\s*(\d{1,6})', user_input) or re.search(r'(\d{1,6})', user_input)
        code = m.group(1) if m else None

        _update_step(thread.id, "codigo_erro_informado", user_input)

        try:
            await thread.send(f"🔎 **Número/erro informado pelo usuário:**\n{user_input}")
        except Exception:
            pass

        solution = (ERROR_DB.get(str(code)) if code else None) or ERROR_DB.get(user_input)

        if solution:
            dm_enviado = False
            try:
                await interaction.user.send(
                    f"💡 **Solução encontrada para o código `{code}`** "
                    f"(chamado: **{getattr(thread, 'name', 'sem nome')}**):\n\n"
                    f"{solution}\n\n"
                    "Caso o problema persista, entre em contato com a equipe de suporte."
                )
                dm_enviado = True
            except discord.Forbidden:
                print(f"[SISTEMAS] DM fechada para {interaction.user}")
            except Exception as e:
                print(f"[SISTEMAS] Erro ao enviar DM: {e}")

            aviso = "✅ Solução encontrada e enviada no privado do usuário.\n"
            if not dm_enviado:
                aviso += "⚠️ Não foi possível enviar DM (DMs fechadas).\n"
            try:
                await thread.send(aviso)
            except Exception:
                pass

            try:
                await thread.remove_user(interaction.user)
            except Exception:
                pass

            await _ping_role(
                thread, guild, config.CHATGURU_ROLE_ID,
                "📋 Solução automática aplicada. Equipe ChatGuru, fiquem cientes do chamado:",
            )

            _update_step(thread.id, "solution_found", "sim")
            _update_step(thread.id, "solution_text", solution)
            _update_step(thread.id, "dm_enviado", "sim" if dm_enviado else "nao")
            return

        # Sem solução → pinga ChatGuru
        await _ping_role(
            thread, guild, config.CHATGURU_ROLE_ID,
            "❗ Não encontrei uma solução automática para esse código. Chamando equipe ChatGuru.",
        )
        await interaction.followup.send(
            "Obrigado, a equipe de T.I foi notificada. (Nenhuma solução automática encontrada.)",
            ephemeral=True,
        )


# ── ProblemTypeView — primeira pergunta dentro da thread ──────────────────────

class ProblemTypeView(discord.ui.View):
    """Compartilhada por todos os sistemas. Roteia para o fluxo correto."""

    def __init__(self, sistema: str, original_user_id: int):
        super().__init__(timeout=None)
        self.sistema = sistema
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Apenas o solicitante pode interagir aqui.", ephemeral=True
            )
            return False
        return True

    async def _iniciar_diagnostico(self, interaction: discord.Interaction, tipo: str) -> None:
        cfg = SISTEMAS_CONFIG[self.sistema]
        thread = interaction.channel

        # Permissão/cadastro → pinga role diretamente
        if tipo == "permissao_cadastro":
            await _disable_view(interaction, self)
            await interaction.response.defer()
            await _ping_role(
                thread, interaction.guild, cfg["role_id"],
                f"🔐 O usuário informou problema de permissão/cadastro, chamando equipe {self.sistema}:",
            )
            return

        # ClickUp lentidão especial
        if tipo == "lentidao_travamento" and cfg.get("lentidao_especial"):
            await _disable_view(interaction, self)
            await interaction.response.defer()
            await thread.send(
                "⚙️ Observação técnica: o ClickUp é hospedado nos EUA e, em períodos de maior "
                "latência, é comum apresentar lentidão ou travamentos pontuais. Algumas ações que ajudam:\n\n"
                "• Tente alternar entre a versão web e o app (Windows Store) — às vezes o app é mais estável.\n"
                "• Limpe cache / força recarga (Ctrl+F5) na versão web.\n"
                "• Verifique se alguma extensão do navegador pode estar interferindo.\n\n"
                "Se preferir que a equipe verifique, escolha uma das opções abaixo:",
                view=ClickupSlowView(self.original_user_id),
            )
            return

        # Fluxo padrão → passo 0 (cache)
        await _disable_view(interaction, self)
        await interaction.response.defer()
        pergunta = _get_pergunta(self.sistema, cfg["steps"][0])
        await thread.send(
            pergunta,
            view=DiagnosticoView(self.sistema, 0, self.original_user_id),
        )

    @discord.ui.button(label="🐢lentidão/travamento🐢", style=discord.ButtonStyle.primary)
    async def slow(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "tipo_problema", "lentidao_travamento")
        await self._iniciar_diagnostico(interaction, "lentidao_travamento")

    @discord.ui.button(label="🌐página não carrega🌐", style=discord.ButtonStyle.success)
    async def page_not_load(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "tipo_problema", "pagina_nao_carrega")
        await self._iniciar_diagnostico(interaction, "pagina_nao_carrega")

    @discord.ui.button(label="🔐permissão/cadastro🔐", style=discord.ButtonStyle.primary)
    async def permission(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "tipo_problema", "permissao_cadastro")
        await self._iniciar_diagnostico(interaction, "permissao_cadastro")

    @discord.ui.button(label="❌mensagem de erro❌", style=discord.ButtonStyle.danger)
    async def error_msg(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "tipo_problema", "mensagem_de_erro")
        await self._iniciar_diagnostico(interaction, "mensagem_de_erro")

    @discord.ui.button(label="⚠️mensagem de aviso⚠️", style=discord.ButtonStyle.success)
    async def warning(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "tipo_problema", "mensagem_de_aviso")
        await self._iniciar_diagnostico(interaction, "mensagem_de_aviso")


# ── ServiceModal ──────────────────────────────────────────────────────────────

class ServiceModal(discord.ui.Modal):
    def __init__(self, sistema: str, original_interaction: discord.Interaction = None):
        super().__init__(title=f"Suporte - {sistema}")
        self.sistema = sistema
        self.original_interaction = original_interaction
        self.descricao = discord.ui.TextInput(
            label=f"Qual problema você está tendo no {sistema}?",
            style=discord.TextStyle.paragraph,
            placeholder="Descreva o problema com o máximo de detalhes possível...",
            required=True,
            max_length=1000,
        )
        self.add_item(self.descricao)

    async def on_submit(self, interaction: discord.Interaction):
        await _handle_modal_submit(
            interaction, self.sistema, self.descricao.value, self.original_interaction
        )


async def _handle_modal_submit(
    interaction: discord.Interaction,
    sistema: str,
    descricao: str,
    original_interaction: discord.Interaction = None,
) -> None:
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    channel = interaction.channel
    user = interaction.user

    if not guild or not channel:
        await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
        return

    thread_name = f"3 - {sistema} - {user.display_name}"

    try:
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
        )
    except Exception as e:
        await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
        print(f"[SISTEMAS] Erro ao criar thread: {e}")
        return

    # Bot entra na thread
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

    embed = discord.Embed(
        title=f"🧩 Chamado - {sistema}",
        description=(
            f"👤 **Usuário:** {user.mention}\n\n"
            f"📝 **Descrição inicial do problema:**\n{descricao}\n\n"
            f"🔎 **Selecione abaixo o tipo que melhor descreve a situação:**"
        ),
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now(),
    )
    embed.add_field(name="🐢 Lentidão / Travamento",  value="O sistema está lento, congelando ou demorando para responder.\n\u200b", inline=False)
    embed.add_field(name="🌐 Página não carrega",      value="A página fica em branco, carregando infinitamente ou retorna erro de acesso.\n\u200b", inline=False)
    embed.add_field(name="🔐 Permissão / Cadastro",    value="Problemas de acesso, bloqueio de usuário, falta de permissão ou necessidade de cadastro.\n\u200b", inline=False)
    embed.add_field(name="❌ Mensagem de erro",         value="Apareceu uma mensagem de erro específica (ex: código, alerta vermelho, falha crítica).\n\u200b", inline=False)
    embed.add_field(name="⚠️ Mensagem de aviso",       value="Apareceu um alerta ou aviso no sistema, mas sem bloquear totalmente o uso.\n\u200b", inline=False)
    embed.set_footer(text="Após selecionar uma opção, o diagnóstico automático será iniciado.")

    try:
        await thread.send(embed=embed)
        await thread.send(
            "Qual o tipo de problema? Escolha uma opção abaixo:",
            view=ProblemTypeView(sistema, user.id),
        )
    except Exception as e:
        print(f"[SISTEMAS] Erro ao enviar mensagens na thread: {e}")

    # Salva payload pendente (aguardando !sistema → SectorSelectView)
    PENDING_PAYLOADS[thread.id] = {
        "event": "topic_created",
        "system": sistema,
        "description": descricao,
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
    print(f"[SISTEMAS] Payload pendente salvo: thread {thread.id}")

    # Apaga o ephemeral do menu após 3s (mesmo padrão do módulo TI)
    if original_interaction:
        await asyncio.sleep(3)
        try:
            await original_interaction.delete_original_response()
        except Exception:
            pass


# ── ServicesView (ephemeral no menu principal) ────────────────────────────────

class ServicesView(discord.ui.View):
    """Exibida como ephemeral quando o usuário clica em Sistemas no menu."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💬ChatGuru💬", style=discord.ButtonStyle.success, custom_id="sistemas_btn_chatguru")
    async def chatguru(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ServiceModal("ChatGuru", original_interaction=interaction))

    @discord.ui.button(label="⚖️Whom⚖️", style=discord.ButtonStyle.primary, custom_id="sistemas_btn_whom")
    async def whom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ServiceModal("Whom", original_interaction=interaction))

    @discord.ui.button(label="⚙️Clickup⚙️", style=discord.ButtonStyle.danger, custom_id="sistemas_btn_clickup")
    async def clickup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ServiceModal("Clickup", original_interaction=interaction))


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

        payload = PENDING_PAYLOADS.pop(self.thread_id, None)
        if payload is None:
            await interaction.followup.send(
                "Payload não encontrado (já enviado?).", ephemeral=True
            )
            return

        payload["setor"] = selected
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

        try:
            await thread.delete()
            print(f"[SISTEMAS] Thread {self.thread_id} deletada após envio.")
        except Exception as e:
            print(f"[SISTEMAS] Não foi possível deletar thread {self.thread_id}: {e}")


# ── Helpers do comando !sistema ───────────────────────────────────────────────

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


# ── setup (registra comando no bot) ──────────────────────────────────────────

def setup(bot: commands.Bot) -> None:
    @bot.command(name="sistema")
    async def sistema_cmd(ctx: commands.Context):
        guild = ctx.guild
        channel = ctx.channel

        if not guild or not isinstance(channel, discord.Thread):
            await ctx.reply(
                "Este comando só pode ser usado dentro de um tópico.", mention_author=False
            )
            return

        if not channel.name.startswith("3 -"):
            await ctx.reply(
                "Este comando só funciona em tópicos de sistemas (prefixo '3 -').",
                mention_author=False,
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
            