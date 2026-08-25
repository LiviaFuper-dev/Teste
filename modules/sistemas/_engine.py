"""
_engine.py — Estado compartilhado, helpers e engine genérica de diagnóstico.

Contém:
  - PENDING_PAYLOADS, SISTEMAS_CONFIG, _STEPS_PADRAO
  - Funções utilitárias usadas por todos os sub-módulos
  - DiagnosticoView  (cache → reiniciar → aguardar 5min)
  - ProblemTypeView  (5 botões de tipo de problema)
"""

import asyncio
import datetime
import json
import os
from typing import Optional

import discord
from discord.ext import commands
import requests

import config

# ── Estado compartilhado ──────────────────────────────────────────────────────
_PAYLOADS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "pending_payloads.json")
PENDING_PAYLOADS: dict[int, dict] = {}


def _load_payloads() -> None:
    """Carrega payloads do disco para a memória."""
    global PENDING_PAYLOADS
    try:
        with open(_PAYLOADS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # JSON salva chaves como string, converter de volta para int
        PENDING_PAYLOADS = {int(k): v for k, v in raw.items()}
        print(f"[PAYLOADS] {len(PENDING_PAYLOADS)} payload(s) carregado(s) do disco.")
    except FileNotFoundError:
        PENDING_PAYLOADS = {}
    except Exception as e:
        print(f"[PAYLOADS] Erro ao carregar payloads: {e}")
        PENDING_PAYLOADS = {}


def _save_payloads() -> None:
    """Persiste payloads da memória para o disco."""
    try:
        os.makedirs(os.path.dirname(_PAYLOADS_FILE), exist_ok=True)
        with open(_PAYLOADS_FILE, "w", encoding="utf-8") as f:
            json.dump(PENDING_PAYLOADS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[PAYLOADS] Erro ao salvar payloads: {e}")


def set_payload(thread_id: int, payload: dict) -> None:
    """Adiciona um payload e persiste no disco."""
    PENDING_PAYLOADS[thread_id] = payload
    _save_payloads()


def pop_payload(thread_id: int) -> dict | None:
    """Remove e retorna um payload, persistindo a remoção."""
    payload = PENDING_PAYLOADS.pop(thread_id, None)
    if payload is not None:
        _save_payloads()
    return payload


# Carregar payloads salvos ao importar o módulo
_load_payloads()

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
    {
        "step_key": "reinicio_pc",
        "pergunta_padrao": (
            "💻 Você já tentou reiniciar o computador? Às vezes um reinício resolve "
            "problemas de conexão e carregamento do sistema."
        ),
    },
    {
        "step_key": "print_tela",
        "pergunta_padrao": "📸 Envie um print da tela inteira onde o erro aparece.",
        "view": "print",
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

# ── Helpers ───────────────────────────────────────────────────────────────────

def _update_step(thread_id: int, key: str, value: str) -> None:
    payload = PENDING_PAYLOADS.get(thread_id)
    if payload is not None:
        payload.setdefault("steps", {})[key] = value
        payload["last_interaction_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        _save_payloads()


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
            await thread.send(
                f"✅ Chamado resolvido pelo usuário. (cargo <@&{role_id}> não encontrado)"
            )
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
    roles = {
        config.CHATGURU_ROLE_ID,
        config.WHOM_ROLE_ID,
        config.CLICKUP_SUPPORT_ROLE_ID,
        config.EMAIL_MLR_ROLE_ID,
        config.EMAIL_GMAIL_ROLE_ID,
        config.TRESCEPLUS_ROLE_ID,
        config.ADMIN_EXTRA_ROLE_ID,
    }
    cargo_ti = cfg.get("cargo_ti")
    if cargo_ti:
        roles.add(int(cargo_ti))
    for key in (
        "chatguru_role_id",
        "whom_role_id",
        "clickup_support_role_id",
        "tresceplus_role_id",
    ):
        role_id = cfg.get(key)
        if role_id:
            roles.add(int(role_id))
    return roles


def _empresa_clickup(guild_id: int) -> tuple[str | None, str | None]:
    raw = str(
        config.SERVIDORES.get(guild_id, {}).get("empresa_clickup")
        or config.SERVIDORES.get(guild_id, {}).get("nome")
        or ""
    ).strip().lower()

    if raw in {"mlr", "mlr_advogados", "mlr advogados"}:
        return "mlr_advogados", "MLR"
    if raw in {"fuper"}:
        return "fuper", "FUPER"
    return None, None


def _member_has_role(member: Optional[discord.Member], role_ids: set[int]) -> bool:
    if member is None:
        return False
    return any(r.id in role_ids for r in member.roles)


# ── DiagnosticoView ───────────────────────────────────────────────────────────

class DiagnosticoView(discord.ui.View):
    """
    Engine genérica: cache → reiniciar → aguardar 5min.
    Ao esgotar os steps, delega para _escalada_final() em __init__.py
    via import lazy para evitar importação circular.
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
            view = (
                PrintScreenView(self.sistema, next_index, self.original_user_id)
                if steps[next_index].get("view") == "print"
                else DiagnosticoView(self.sistema, next_index, self.original_user_id)
            )
            await interaction.channel.send(
                pergunta,
                view=view,
            )
        else:
            await interaction.response.defer()
            # Import lazy para evitar circular: __init__ importa _engine, não o contrário
            from . import _escalada_final
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
            3: "Boa, reiniciar o PC resolveu! Se precisar, estamos aqui. 💻",
        }
        await interaction.response.send_message(
            msgs.get(self.step_index, "Ótimo, problema resolvido!"), ephemeral=True
        )
        await _finalizar_resolvido(interaction, cfg["role_id"])


# ── ProblemTypeView ───────────────────────────────────────────────────────────

class PrintScreenView(discord.ui.View):
    """Confirma se o solicitante anexou um print antes da escalada final."""

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

    async def _continuar(self, interaction: discord.Interaction, resposta: str) -> None:
        if not await self._check_user(interaction):
            return

        cfg = SISTEMAS_CONFIG[self.sistema]
        steps = cfg["steps"]
        _update_step(interaction.channel.id, steps[self.step_index]["step_key"], resposta)
        await _disable_view(interaction, self)
        await interaction.response.defer()

        next_index = self.step_index + 1
        if next_index < len(steps):
            pergunta = _get_pergunta(self.sistema, steps[next_index])
            view = (
                PrintScreenView(self.sistema, next_index, self.original_user_id)
                if steps[next_index].get("view") == "print"
                else DiagnosticoView(self.sistema, next_index, self.original_user_id)
            )
            await interaction.channel.send(pergunta, view=view)
            return

        from . import _escalada_final
        await _escalada_final(interaction, self.sistema, cfg)

    @discord.ui.button(label="Não tem print", style=discord.ButtonStyle.danger)
    async def nao_tem_print(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._continuar(interaction, "nao_tem_print")

    @discord.ui.button(label="Já enviei", style=discord.ButtonStyle.primary)
    async def ja_enviei(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._continuar(interaction, "ja_enviei")


class ProblemTypeView(discord.ui.View):
    """5 botões de tipo de problema. Roteia para o fluxo correto de cada sistema."""

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

        if tipo == "permissao_cadastro":
            await _disable_view(interaction, self)
            await interaction.response.defer()

            pergunta = _get_pergunta(self.sistema, cfg["steps"][0])
            await thread.send(
                pergunta,
                view=DiagnosticoView(self.sistema, 0, self.original_user_id),
            )
            return

        if tipo == "lentidao_travamento" and cfg.get("lentidao_especial"):
            await _disable_view(interaction, self)
            await interaction.response.defer()
            # Import lazy — ClickupSlowView está em _clickup, que importa de _engine
            from ._clickup import ClickupSlowView
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
