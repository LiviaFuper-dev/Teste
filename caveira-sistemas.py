# caveira-sistemas.py
import os
import io
import asyncio
import discord
from discord.ext import commands
import datetime
from typing import Optional
import requests
import json
from dotenv import load_dotenv
import os
import re

# ===== CONFIG =====
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
# Canal onde a mensagem com os botões será enviada
CANAL_ID = 1479472739176808559

# Servidores/config por guild
SERVIDORES = {
    1407051681421594806: {  # ID do servidor
        "nome": "Servidor-Alvo",
        "canal_suporte": 1479472739176808559,
        "cargo_ti": 1429922070036217977,
    }
}
# ==================

# ID do cargo específico ChatGuru (quando for permissão/cadastro)
CHATGURU_ROLE_ID = 1474430718582067320
# ID do cargo específico Whom (quando for permissão/cadastro ou falha final)
WHOM_ROLE_ID = 1474430877827203165
# ID do suporte Clickup (pedido seu)
CLICKUP_SUPPORT_ROLE_ID = 1474431104046989312

# URL do webhook n8n
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL_SIS")
PENDING_PAYLOADS = {} 
# arquivo JSON com soluções (coloque solutions.json no mesmo diretório)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOLUTIONS_FILE = os.path.join(BASE_DIR, "solutions.json")

THREAD_AUTO_ARCHIVE_MINUTES = 1440  # 24h

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# carregar DB de soluções uma vez
try:
    with open(SOLUTIONS_FILE, "r", encoding="utf-8") as f:
        ERROR_DB = json.load(f)
        # garantir chaves como strings (se forem números no json)
        ERROR_DB = {str(k): v for k, v in ERROR_DB.items()}
    print(f"[DEBUG] solutions.json carregado — {len(ERROR_DB)} entradas.")
except FileNotFoundError:
    ERROR_DB = {}
    print(f"[WARN] {SOLUTIONS_FILE} não encontrado. ERROR_DB vazio.")
except Exception as e:
    ERROR_DB = {}
    print(f"[WARN] erro ao carregar {SOLUTIONS_FILE}: {e}")


def get_config_for_guild(guild_id: int):
    return SERVIDORES.get(guild_id)


def resolve_cargo_ti(guild: discord.Guild, cfg: dict):
    expected_id = cfg.get("cargo_ti")
    if expected_id:
        role = guild.get_role(expected_id)
        if role:
            return role, "id"
    for role in guild.roles:
        name = (role.name or "").lower()
        if any(k in name for k in ["ti", "t.i", "tecnico", "suporte"]):
            return role, f"fallback_name:{role.name}"
    return None, "not_found"


async def disable_view(interaction: discord.Interaction, view: discord.ui.View):
    for item in view.children:
        try:
            item.disabled = True
        except Exception:
            pass
    try:
        if getattr(interaction, "message", None):
            await interaction.message.edit(view=view)
    except Exception as e:
        print(f"[WARN] falha ao desabilitar view: {e}")


async def safe_join_thread(thread: discord.Thread):
    try:
        await thread.join()
        await asyncio.sleep(0.15)
        return True, "joined"
    except Exception as e:
        try:
            await thread.add_user(bot.user)
            await asyncio.sleep(0.15)
            return True, "add_user_fallback"
        except Exception as e2:
            print(f"[WARN] não conseguiu entrar na thread: {e} / fallback: {e2}")
            return False, "failed"

def member_has_any_role(member: discord.Member, role_id_set: set) -> bool:
    if member is None:
        return False
    try:
        return any(r.id in role_id_set for r in member.roles)
    except Exception:
        return False

async def remove_non_allowed_members(thread: discord.Thread, guild: discord.Guild, allowed_roles: set[int]):
    removed = []
    failed = []

    try:
        await thread.fetch_members()
    except Exception as e:
        print(f"[WARN] Não foi possível atualizar membros da thread: {e}")

    for thread_member in thread.members:
        try:
            member = guild.get_member(thread_member.id)
            if member is None:
                try:
                    member = await guild.fetch_member(thread_member.id)
                except Exception:
                    continue

            if member.bot:
                continue

            has_permission = any(role.id in allowed_roles for role in member.roles)

            if not has_permission:
                try:
                    await thread.remove_user(member)
                    removed.append(member.id)
                except Exception as e:
                    print(f"[ERRO] Falha ao remover {member.id}: {e}")
                    failed.append(member.id)

        except Exception as e:
            print(f"[ERRO] Problema ao processar membro {thread_member.id}: {e}")
            failed.append(thread_member.id)

    return removed, failed

async def send_to_n8n(payload: dict) -> bool:
    try:
        def _post():
            r = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=10)
            r.raise_for_status()
            return r

        resp = await asyncio.to_thread(_post)
        print(f"[DEBUG] Enviado para n8n, status: {resp.status_code}")
        return True
    except Exception as e:
        print(f"[WARN] falha ao enviar para n8n: {e}")
        return False


async def finalizar_resolvido(interaction: discord.Interaction, role_id: int):
    """
    Chamado em todo botão 'Resolveu':
    1. Remove o usuário do tópico
    2. Pinga o cargo responsável pelo sistema
    """
    thread = interaction.channel
    guild = interaction.guild

    try:
        await thread.remove_user(interaction.user)
        print(f"[DEBUG] Usuário {interaction.user} removido da thread {thread.id} (resolvido)")
    except Exception as e:
        print(f"[WARN] Não foi possível remover o usuário da thread: {e}")

    try:
        role = guild.get_role(role_id) if guild else None
        if role:
            await thread.send(
                f"✅ Chamado resolvido pelo usuário. {role.mention}",
                allowed_mentions=discord.AllowedMentions(roles=True)
            )
        else:
            await thread.send(f"✅ Chamado resolvido pelo usuário. (cargo <@&{role_id}> não encontrado)")
    except Exception as e:
        print(f"[WARN] Erro ao pingar cargo após resolução: {e}")


# ===== NOVA FUNÇÃO: registra cada passo do diagnóstico no payload pendente =====
def update_payload_step(thread_id: int, step: str, value: str):
    """
    Adiciona/atualiza um passo do diagnóstico no payload pendente da thread.
    Os dados ficam em payload['steps'] como dict step -> value.
    Exemplo: {'cache_limpo': 'nao_resolveu', 'reinicio_pagina': 'resolveu'}
    """
    payload = PENDING_PAYLOADS.get(thread_id)
    if payload is not None:
        if "steps" not in payload:
            payload["steps"] = {}
        payload["steps"][step] = value
        print(f"[DEBUG] step registrado thread {thread_id}: {step} = {value}")
# ==============================================================================


class SectorSelectView(discord.ui.View):
    def __init__(self, original_guild_id: int, thread_id: int, timeout: float = None):
        super().__init__(timeout=timeout)
        self.original_guild_id = original_guild_id
        self.thread_id = thread_id

        options = [
            discord.SelectOption(label="Comercial", value="Comercial"),
            discord.SelectOption(label="Administrativo", value="Administrativo"),
            discord.SelectOption(label="Jurídico", value="Jurídico"),
            discord.SelectOption(label="Financeiro", value="Financeiro"),
            discord.SelectOption(label="RH", value="RH"),
            discord.SelectOption(label="Marketing", value="Marketing"),
            discord.SelectOption(label="TI", value="TI"),
            discord.SelectOption(label="Todos", value="Todos"),
        ]

        select = discord.ui.Select(
            placeholder="Selecione o setor do colaborador",
            options=options,
            custom_id=f"sector_select_{thread_id}",
            min_values=1,
            max_values=1
        )

        async def _select_callback(interaction: discord.Interaction):
            await self._on_select(select, interaction)

        select.callback = _select_callback
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Não foi possível identificar servidor.", ephemeral=True)
            return False

        cfg = get_config_for_guild(guild.id) or {}
        cargo_ti_id = cfg.get("cargo_ti")
        allowed = {CHATGURU_ROLE_ID, WHOM_ROLE_ID, CLICKUP_SUPPORT_ROLE_ID}
        try:
            if cargo_ti_id is not None:
                allowed.add(int(cargo_ti_id))
        except Exception:
            pass

        member = guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await guild.fetch_member(interaction.user.id)
            except Exception:
                member = None

        if member is None:
            await interaction.response.send_message(
                "Não foi possível acessar os cargos do usuário. Verifique intents e permissões do bot.",
                ephemeral=True
            )
            return False

        if member_has_any_role(member, allowed):
            return True

        await interaction.response.send_message("Apenas membros autorizados (TI/ChatGuru/Whom/ClickUp) podem preencher este formulário.", ephemeral=True)
        return False

    async def _on_select(self, select: discord.ui.Select, interaction: discord.Interaction):
        selected = select.values[0] if select.values else None
        await interaction.response.defer(ephemeral=True)

        payload = PENDING_PAYLOADS.pop(self.thread_id, None)
        if payload is None:
            await interaction.followup.send("Payload não encontrado (talvez já tenha sido enviado).", ephemeral=True)
            return

        payload["setor"] = selected

        try:
            ok = await send_to_n8n(payload)
            if not ok:
                raise RuntimeError("send_to_n8n retornou False")
        except Exception as e:
            print(f"[ERROR] erro ao enviar payload para n8n: {e}")
            await interaction.followup.send("Erro ao enviar os dados para o fluxo (n8n). Verifique logs.", ephemeral=True)
            return

        try:
            thread = interaction.channel
            await thread.send(f"Formulário enviado. Setor definido como **{selected}**. O pedido foi encaminhado para processamento.")
        except Exception:
            pass

        await interaction.followup.send("Formulário recebido e encaminhado com sucesso.", ephemeral=True)

        try:
            thread = interaction.channel
            try:
                await thread.send(f"Formulário recebido. Setor definido como **{selected}**. Encerrando o tópico.")
            except Exception:
                pass
        except Exception:
            thread = None

        try:
            if thread is not None:
                await thread.delete()
                print(f"[DEBUG] tópico {self.thread_id} deletado após envio do formulário.")
        except Exception as e:
            print(f"[WARN] não foi possível deletar o tópico {self.thread_id}: {e}")

        self.stop()


@bot.command(name="sistema")
async def sistema_cmd(ctx: commands.Context):
    guild = ctx.guild
    channel = ctx.channel

    if guild is None or channel is None:
        await ctx.reply("⚠️ Não foi possível identificar servidor/canal.", mention_author=False)
        return

    cfg = get_config_for_guild(guild.id) or {}
    cargo_ti_id = cfg.get("cargo_ti")
    if cargo_ti_id is None:
        await ctx.reply("⚠️ Cargo de TI não configurado para este servidor.", mention_author=False)
        return

    if not isinstance(channel, discord.Thread):
        await ctx.reply("Este comando só pode ser usado dentro de um tópico (thread).", mention_author=False)
        return

    try:
        cargo_ti_id = int(cargo_ti_id) if cargo_ti_id is not None else None
    except Exception:
        cargo_ti_id = None

    allowed_roles = {CHATGURU_ROLE_ID, WHOM_ROLE_ID, CLICKUP_SUPPORT_ROLE_ID}
    if cargo_ti_id:
        allowed_roles.add(cargo_ti_id)

    member = ctx.author
    if not isinstance(member, discord.Member):
        try:
            member = await guild.fetch_member(ctx.author.id)
        except Exception:
            member = None

    if member is None:
        await ctx.reply("Não foi possível validar seus cargos no servidor.", mention_author=False)
        return

    if not member_has_any_role(member, allowed_roles):
        await ctx.reply("Apenas membros autorizados (TI/ChatGuru/Whom/ClickUp) podem executar este comando.", mention_author=False)
        return

    removed, failed = await remove_non_allowed_members(channel, guild, allowed_roles)
    summary = f"Removidos: {len(removed)}."
    if failed:
        summary += f" Falhas: {len(failed)} (veja logs)."
    try:
        await ctx.reply(summary, mention_author=False)
    except Exception:
        pass

    if PENDING_PAYLOADS.get(channel.id):
        try:
            view = SectorSelectView(original_guild_id=guild.id, thread_id=channel.id, timeout=None)
            await channel.send("Por favor, selecione o **Setor do colaborador** para prosseguir com o encaminhamento.", view=view)
        except Exception as e:
            print(f"[WARN] não foi possível enviar o mini-form no tópico: {e}")
            try:
                await ctx.author.send("Por favor, selecione o Setor do colaborador.", view=view)
            except Exception:
                await ctx.reply("❌ Não foi possível abrir o formulário aqui nem em DM.", mention_author=False)
    else:
        try:
            await ctx.reply("Nenhum payload pendente para este tópico (talvez já tenha sido enviado).", mention_author=False)
        except Exception:
            pass


# ----- Modal para descrição inicial -----
class ServiceModal(discord.ui.Modal):
    def __init__(self, sistema: str):
        super().__init__(title=f"Suporte - {sistema}")
        self.sistema = sistema
        self.descricao = discord.ui.TextInput(
            label=f"Qual problema você está tendo no {sistema}?",
            style=discord.TextStyle.paragraph,
            placeholder="Descreva o problema com o máximo de detalhes possível...",
            required=True,
            max_length=1000
        )
        self.add_item(self.descricao)

    async def on_submit(self, interaction: discord.Interaction):
        await handle_service_modal_submit(interaction, self.sistema, self.descricao.value)

class CallHelpView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Apenas o solicitante pode chamar ajuda.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Chamar T.I",
        style=discord.ButtonStyle.danger,
    )
    async def call_help(self, interaction: discord.Interaction, button: discord.ui.Button):

        guild = interaction.guild
        thread = interaction.channel

        role = guild.get_role(CHATGURU_ROLE_ID) if guild else None

        if role:
            await thread.send(
                "🆘 O usuário solicitou ajuda adicional. Chamando equipe ChatGuru:",
                allowed_mentions=discord.AllowedMentions(roles=True)
            )
            await thread.send(
                role.mention,
                allowed_mentions=discord.AllowedMentions(roles=True)
            )
        else:
            await thread.send(
                "🆘 O usuário solicitou ajuda adicional. Chamando equipe ChatGuru."
            )

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(view=self)


# ----- Modal para número de erro (última etapa) -----
class ErrorNumberModal(discord.ui.Modal):
    def __init__(self, original_user_id: int):
        super().__init__(title="Número do erro")
        self.original_user_id = original_user_id

        self.error_number = discord.ui.TextInput(
            label="Informe o número do erro",
            placeholder="Ex: 131049 - mensagem...",
            style=discord.TextStyle.short,
            required=True,
            max_length=200
        )

        self.add_item(self.error_number)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante"
            " pode enviar este dado.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        thread = interaction.channel
        guild = interaction.guild
        cfg = get_config_for_guild(guild.id) if guild else {}

        user_input = self.error_number.value.strip()
        m = re.match(r'^\s*(\d{1,6})', user_input)
        if not m:
            m = re.search(r'(\d{1,6})', user_input)
        code = m.group(1) if m else None

        # registrar o código informado no payload
        update_payload_step(thread.id, "codigo_erro_informado", user_input)

        try:
            await thread.send(f"🔎 **Número/erro informado pelo usuário:**\n{user_input}")
        except Exception as e:
            print(f"[WARN] erro ao enviar número na thread: {e}")

        solution = None
        if code:
            solution = ERROR_DB.get(str(code))
        if not solution:
            solution = ERROR_DB.get(user_input)

        payload = {
            "event": "error_number_submitted",
            "user_id": interaction.user.id,
            "user_name": interaction.user.display_name,
            "user_tag": str(interaction.user),
            "guild_id": getattr(guild, "id", None),
            "guild_name": getattr(guild, "name", None),
            "thread_id": getattr(thread, "id", None),
            "thread_name": getattr(thread, "name", None),
            "error_input": user_input,
            "error_code_extracted": code,
            "solution_found": bool(solution),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }

        if solution:
            # 1) Envia a solução no privado do usuário que abriu o chamado
            dm_enviado = False
            try:
                user_obj = interaction.user
                dm_msg = (
                    f"💡 **Solução encontrada para o código `{code}`** "
                    f"(chamado: **{getattr(thread, 'name', 'sem nome')}**):\n\n"
                    f"{solution}\n\n"
                    "Caso o problema persista, entre em contato com a equipe de suporte."
                )
                await user_obj.send(dm_msg)
                dm_enviado = True
                print(f"[DEBUG] Solução enviada no privado de {user_obj} (thread {thread.id})")
            except discord.Forbidden:
                print(f"[WARN] Não foi possível enviar DM para {interaction.user} — DMs fechadas.")
            except Exception as e:
                print(f"[WARN] Erro ao enviar DM com solução: {e}")

            # 2) Avisa na thread que a solução foi enviada no privado (visível para equipe)
            try:
                aviso = (
                    "✅ Solução encontrada e enviada no privado do usuário.\n"
                    + ("" if dm_enviado else "⚠️ Não foi possível enviar DM (DMs fechadas).\n")
                )
                await thread.send(aviso)
            except Exception as e:
                print(f"[WARN] Erro ao enviar aviso na thread: {e}")

            # 3) Remove o usuário do tópico
            try:
                await thread.remove_user(interaction.user)
                print(f"[DEBUG] Usuário {interaction.user} removido da thread {thread.id}")
            except Exception as e:
                print(f"[WARN] Não foi possível remover o usuário da thread: {e}")

            # 4) Pinga o cargo ChatGuru para que a equipe fique ciente
            try:
                role = guild.get_role(CHATGURU_ROLE_ID) if guild else None
                if role:
                    await thread.send(
                        f"📋 Solução automática aplicada. Equipe ChatGuru, fiquem cientes do chamado:",
                        allowed_mentions=discord.AllowedMentions(roles=True)
                    )
                    await thread.send(
                        role.mention,
                        allowed_mentions=discord.AllowedMentions(roles=True)
                    )
                else:
                    print(f"[WARN] Cargo ChatGuru ({CHATGURU_ROLE_ID}) não encontrado na guild.")
            except Exception as e:
                print(f"[WARN] Erro ao pingar ChatGuru após solução: {e}")

            # 5) Atualiza payload e envia para n8n
            update_payload_step(thread.id, "solution_found", "sim")
            update_payload_step(thread.id, "solution_text", solution)
            update_payload_step(thread.id, "dm_enviado", "sim" if dm_enviado else "nao")
            payload["solution_text"] = solution
            await send_to_n8n(payload)
            return

        role = guild.get_role(CHATGURU_ROLE_ID) if guild else None

        if role:
            try:
                await thread.send(
                    "❗ Não encontrei uma solução automática para esse código. "
                    "Chamando equipe ChatGuru.",
                    allowed_mentions=discord.AllowedMentions(roles=True)
                )
                await thread.send(
                    role.mention,
                    allowed_mentions=discord.AllowedMentions(roles=True)
                )
            except Exception as e:
                print(f"[WARN] não conseguiu mencionar ChatGuru: {e}")
        else:
            try:
                await thread.send(
                    "❗ Não encontrei uma solução automática para esse código. "
                    " Chamando equipe Chatguru."
                )
            except Exception as e:
                print(f"[WARN] erro ao enviar aviso sem role ChatGuru: {e}")

        await send_to_n8n(payload)

        await interaction.followup.send("Obrigado, a equipe de T.I foi notificada. "
        "(Nenhuma solução automática encontrada.)", ephemeral=True)


# ----- ProblemTypeView: primeira pergunta para ChatGuru -----
class ProblemTypeView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🐢lentidão/travamento🐢", style=discord.ButtonStyle.primary)
    async def slow(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "lentidao_travamento")
        await disable_view(interaction, self)
        thread = interaction.channel
        await thread.send(
            "Olá tudo bem? Vi que você está tendo problema no ChatGuru, tô aqui "
            "pra ajudar. Deixa eu perguntar: você já limpou o cache? "
            "(tente limpar caso não tenha feito ainda)",
            view=ChatGuruFirstView(self.original_user_id)
        )

    @discord.ui.button(label="🌐página não carrega🌐", style=discord.ButtonStyle.success)
    async def page_not_load(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "pagina_nao_carrega")
        await disable_view(interaction, self)
        thread = interaction.channel
        await thread.send(
            "Olá tudo bem? Vi que você está tendo problema no ChatGuru, tô aqui pra ajudar. "
            "Deixa eu perguntar: você já limpou o cache? (tente limpar caso não tenha feito ainda)",
            view=ChatGuruFirstView(self.original_user_id)
        )

    @discord.ui.button(label="🔐permissão/cadastro🔐", style=discord.ButtonStyle.primary)
    async def permission(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "permissao_cadastro")
        await disable_view(interaction, self)
        thread = interaction.channel
        guild = interaction.guild

        role = None
        try:
            if guild:
                role = guild.get_role(CHATGURU_ROLE_ID)
        except Exception:
            role = None

        if role:
            try:
                await thread.send("🔐 O usuário informou problema de permissão/cadastro:", allowed_mentions=discord.AllowedMentions(roles=True))
                await thread.send(role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
            except Exception as e:
                print(f"[WARN] falha ao mencionar ChatGuru role: {e}")
                await thread.send("🔐 O usuário informou problema de permissão/cadastro.")
        else:
            cfg = get_config_for_guild(guild.id) if guild else {}
            cargo_role, _ = resolve_cargo_ti(guild, cfg) if guild else (None, None)
            if cargo_role:
                try:
                    await thread.send("🔐 O usuário informou problema de permissão/cadastro, chamando equipe de suporte:", allowed_mentions=discord.AllowedMentions(roles=True))
                    await thread.send(cargo_role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
                except Exception as e:
                    print(f"[WARN] falha ao mencionar cargo_ti: {e}")
                    await thread.send("🔐 O usuário informou problema de permissão/cadastro."
                    " Por favor, equipe, verifiquem este chamado.")
            else:
                await thread.send("🔐 O usuário informou problema de permissão/cadastro. "
                "Por favor, equipe ChatGuru, verifiquem este chamado.")

        await interaction.response.send_message("Equipe ChatGuru foi acionada para permissões/cadastro.", ephemeral=True)

    @discord.ui.button(label="❌mensagem de erro❌", style=discord.ButtonStyle.danger)
    async def error_msg(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "mensagem_de_erro")
        await disable_view(interaction, self)
        thread = interaction.channel
        await thread.send(
            "Olá tudo bem? Vi que você está tendo problema no ChatGuru, tô aqui pra ajudar "
            "Deixa eu perguntar: você já limpou o cache? (tente limpar caso não tenha feito ainda)",
            view=ChatGuruFirstView(self.original_user_id)
        )

    @discord.ui.button(label="⚠️mensagem de aviso⚠️", style=discord.ButtonStyle.success)
    async def warning(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "mensagem_de_aviso")
        await disable_view(interaction, self)
        thread = interaction.channel
        await thread.send(
            "Olá tudo bem? Vi que você está tendo problema no ChatGuru, tô aqui pra ajudar. "
            "Deixa eu perguntar: você já limpou o cache? (tente limpar caso não tenha feito ainda)",
            view=ChatGuruFirstView(self.original_user_id)
        )


# ----- ProblemTypeView para Whom -----
class ProblemTypeViewWhom(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🐢lentidão/travamento🐢", style=discord.ButtonStyle.primary)
    async def slow(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "lentidao_travamento")
        await disable_view(interaction, self)
        thread = interaction.channel
        await thread.send(
            "Você já tentou limpar o cache da página? Limpando o cachê o site pode voltar ao normal, pois é um reset de dados.",
            view=WhomFirstView(self.original_user_id)
        )

    @discord.ui.button(label="🌐página não carrega🌐", style=discord.ButtonStyle.secondary)
    async def page_not_load(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "pagina_nao_carrega")
        await disable_view(interaction, self)
        thread = interaction.channel
        await thread.send(
            "Você já tentou limpar o cache da página? Limpando o cachê o site pode voltar ao normal, pois é um reset de dados.",
            view=WhomFirstView(self.original_user_id)
        )

    @discord.ui.button(label="🔐permissão/cadastro🔐", style=discord.ButtonStyle.primary)
    async def permission(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "permissao_cadastro")
        await disable_view(interaction, self)
        thread = interaction.channel
        guild = interaction.guild

        role = None
        try:
            if guild:
                role = guild.get_role(WHOM_ROLE_ID)
        except Exception:
            role = None

        if role:
            try:
                await thread.send("🔐 O usuário informou problema de permissão/cadastro, chamando equipe Whom:", allowed_mentions=discord.AllowedMentions(roles=True))
                await thread.send(role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
            except Exception as e:
                print(f"[WARN] falha ao mencionar Whom role: {e}")
                await thread.send("🔐 O usuário informou problema de permissão/cadastro. Por favor, equipe Whom, verifiquem este chamado.")
        else:
            cfg = get_config_for_guild(guild.id) if guild else {}
            cargo_role, _ = resolve_cargo_ti(guild, cfg) if guild else (None, None)
            if cargo_role:
                try:
                    await thread.send("🔐 O usuário informou problema de permissão/cadastro, chamando equipe de suporte:", allowed_mentions=discord.AllowedMentions(roles=True))
                    await thread.send(cargo_role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
                except Exception as e:
                    print(f"[WARN] falha ao mencionar cargo_ti: {e}")
                    await thread.send("🔐 O usuário informou problema de permissão/cadastro."
                    " Por favor, equipe, verifiquem este chamado.")
            else:
                await thread.send("🔐 O usuário informou problema de permissão/cadastro."
                " Por favor, equipe Whom, verifiquem este chamado.")

        await interaction.response.send_message("Equipe Whom foi acionada para permissões/cadastro.", ephemeral=True)

    @discord.ui.button(label="❌mensagem de erro❌", style=discord.ButtonStyle.danger)
    async def error_msg(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "mensagem_de_erro")
        await disable_view(interaction, self)
        thread = interaction.channel
        await thread.send(
            "Você já tentou limpar o cache da página? Limpando o cachê o site pode voltar ao normal, pois é um reset de dados.",
            view=WhomFirstView(self.original_user_id)
        )

    @discord.ui.button(label="⚠️mensagem de aviso⚠️", style=discord.ButtonStyle.success)
    async def warning(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "mensagem_de_aviso")
        await disable_view(interaction, self)
        thread = interaction.channel
        await thread.send(
            "Você já tentou limpar o cache da página? Limpando o cachê o site pode voltar ao normal, pois é um reset de dados.",
            view=WhomFirstView(self.original_user_id)
        )


# ----- VIEWS do fluxo Whom -----
class WhomFirstView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Não resolveu", style=discord.ButtonStyle.danger)
    async def no_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "cache_limpo", "nao_resolveu")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        await thread.send(
            "🤔 Já tentou reiniciar a página? (Ctrl + F5 ou atualizar com o botão no canto superior esquerdo) (isso atualiza a página)",
            view=WhomSecondView(self.original_user_id)
        )

    @discord.ui.button(label="Resolveu", style=discord.ButtonStyle.success)
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "cache_limpo", "resolveu")
        await disable_view(interaction, self)
        await interaction.response.send_message("Ótimo! Fico feliz que tenha resolvido. Se precisar, reabra o chamado.", ephemeral=True)
        await finalizar_resolvido(interaction, WHOM_ROLE_ID)


class WhomSecondView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Não resolveu", style=discord.ButtonStyle.danger)
    async def no_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "reinicio_pagina", "nao_resolveu")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        await thread.send(
            "⏳ Às vezes o problema é interno do sistema, já está esperando há cerca de 5 minutos e não resolveu?",
            view=WhomThirdView(self.original_user_id)
        )

    @discord.ui.button(label="Resolveu", style=discord.ButtonStyle.success)
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "reinicio_pagina", "resolveu")
        await disable_view(interaction, self)
        await interaction.response.send_message("Perfeito, que bom que resolveu! 👍", ephemeral=True)
        await finalizar_resolvido(interaction, WHOM_ROLE_ID)


class WhomThirdView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Não resolveu", style=discord.ButtonStyle.danger)
    async def no_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "aguardou_5min", "nao_resolveu")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        await thread.send(
            "🚨 A extensão do Whom apresenta algum aviso em vermelho ou amarelo?"
            " Tente clicar no botão 'status' (fica lá em baixo na extensão)"
            " Normalmente são ajustes internos e o tribunal volta. Isso aparece na extensão do navegador?",
            view=WhomWarningView(self.original_user_id)
        )

    @discord.ui.button(label="Resolveu", style=discord.ButtonStyle.success)
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "aguardou_5min", "resolveu")
        await disable_view(interaction, self)
        await interaction.response.send_message("Ótimo, que bom que resolveu!", ephemeral=True)
        await finalizar_resolvido(interaction, WHOM_ROLE_ID)


class WhomWarningView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="não funcionou", style=discord.ButtonStyle.danger)
    async def not_work(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "aviso_extensao_whom", "nao_funcionou")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        guild = interaction.guild

        role = None
        try:
            if guild:
                role = guild.get_role(WHOM_ROLE_ID)
        except Exception:
            role = None

        if role:
            try:
                await thread.send("❗ O usuário informou que a extensão mostra aviso e as tentativas não funcionaram. Chamando equipe Whom:", allowed_mentions=discord.AllowedMentions(roles=True))
                await thread.send(role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
            except Exception as e:
                print(f"[WARN] falha ao mencionar Whom role: {e}")
                await thread.send("❗ A extensão indica aviso e as tentativas não funcionaram. Por favor, equipe Whom, verifiquem este chamado.")
        else:
            cfg = get_config_for_guild(guild.id) if guild else {}
            cargo_role, _ = resolve_cargo_ti(guild, cfg) if guild else (None, None)
            if cargo_role:
                try:
                    await thread.send("❗ A extensão indica aviso e as tentativas não funcionaram. Chamando suporte:", allowed_mentions=discord.AllowedMentions(roles=True))
                    await thread.send(cargo_role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
                except Exception:
                    await thread.send("❗ A extensão indica aviso e as tentativas não "
                    "funcionaram. Por favor, verifiquem este chamado.")
            else:
                await thread.send("❗ A extensão indica aviso e as tentativas não funcionaram."
                " Por favor, equipe Whom, verifiquem este chamado.")

    @discord.ui.button(label="resolveu", style=discord.ButtonStyle.success)
    async def solved(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "aviso_extensao_whom", "resolveu")
        await disable_view(interaction, self)
        await interaction.response.send_message("Ótimo, que bom que resolveu!", ephemeral=True)
        await finalizar_resolvido(interaction, WHOM_ROLE_ID)


# ----- VIEWS específicas do fluxo ClickUp -----
class ClickupFirstView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Não resolveu", style=discord.ButtonStyle.danger)
    async def no_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "cache_limpo", "nao_resolveu")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        await thread.send(
            "🤔 Já tentou reiniciar a página? (Ctrl + F5 ou atualizar com o botão no canto superior esquerdo) (isso atualiza a página)",
            view=ClickupSecondView(self.original_user_id)
        )

    @discord.ui.button(label="Resolveu", style=discord.ButtonStyle.success)
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "cache_limpo", "resolveu")
        await disable_view(interaction, self)
        await interaction.response.send_message("Ótimo! Fico feliz que tenha resolvido. Se precisar, reabra o chamado.", ephemeral=True)
        await finalizar_resolvido(interaction, CLICKUP_SUPPORT_ROLE_ID)


class ClickupSecondView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Não resolveu", style=discord.ButtonStyle.danger)
    async def no_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "reinicio_pagina", "nao_resolveu")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        await thread.send(
            "⏳ Às vezes o problema é interno do sistema, já está esperando há cerca de 5 minutos e não resolveu?",
            view=ClickupThirdView(self.original_user_id)
        )

    @discord.ui.button(label="Resolveu", style=discord.ButtonStyle.success)
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "reinicio_pagina", "resolveu")
        await disable_view(interaction, self)
        await interaction.response.send_message("Perfeito, que bom que resolveu! 👍", ephemeral=True)
        await finalizar_resolvido(interaction, CLICKUP_SUPPORT_ROLE_ID)


class ClickupThirdView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Não resolveu", style=discord.ButtonStyle.danger)
    async def no_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "aguardou_5min", "nao_resolveu")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        guild = interaction.guild

        role = None
        try:
            if guild:
                role = guild.get_role(CLICKUP_SUPPORT_ROLE_ID)
        except Exception:
            role = None

        if role:
            try:
                await thread.send("❗ O usuário informou que tentou as tentativas sugeridas e não funcionou. Chamando equipe ClickUp:", allowed_mentions=discord.AllowedMentions(roles=True))
                await thread.send(role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
            except Exception as e:
                print(f"[WARN] falha ao mencionar Clickup support role: {e}")
                try:
                    await thread.send(f"❗ Chamando equipe ClickUp: <@&{CLICKUP_SUPPORT_ROLE_ID}>")
                except Exception:
                    await thread.send("❗ Chamando equipe ClickUp (não foi possível mencionar automaticamente).")
        else:
            try:
                await thread.send(f"❗ Chamando equipe ClickUp: <@&{CLICKUP_SUPPORT_ROLE_ID}>")
            except Exception:
                await thread.send("❗ Chamando equipe ClickUp (role não encontrada).")

    @discord.ui.button(label="Resolveu", style=discord.ButtonStyle.success)
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "aguardou_5min", "resolveu")
        await disable_view(interaction, self)
        await interaction.response.send_message("Ótimo, que bom que resolveu!", ephemeral=True)
        await finalizar_resolvido(interaction, CLICKUP_SUPPORT_ROLE_ID)


# ----- VIEWS do fluxo ChatGuru -----
class ChatGuruFirstView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Não resolveu", style=discord.ButtonStyle.danger)
    async def no_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "cache_limpo", "nao_resolveu")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        await thread.send(
            "🤔 Já tentou reiniciar a página? (Ctrl + F5 ou atualizar com o botão no canto superior esquerdo) (isso atualiza a página)",
            view=ChatGuruSecondView(self.original_user_id)
        )

    @discord.ui.button(label="Resolveu", style=discord.ButtonStyle.success)
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "cache_limpo", "resolveu")
        await disable_view(interaction, self)
        await interaction.response.send_message("Ótimo! Fico feliz que tenha resolvido. Se precisar, reabra o chamado.", ephemeral=True)
        await finalizar_resolvido(interaction, CHATGURU_ROLE_ID)


class ChatGuruSecondView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Não resolveu", style=discord.ButtonStyle.danger)
    async def no_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "reinicio_pagina", "nao_resolveu")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        await thread.send(
            "⏳ Às vezes o problema é interno do sistema, já está esperando há 5 minutos e não resolveu?",
            view=ChatGuruThirdView(self.original_user_id)
        )

    @discord.ui.button(label="Resolveu", style=discord.ButtonStyle.success)
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "reinicio_pagina", "resolveu")
        await disable_view(interaction, self)
        await interaction.response.send_message("Perfeito, que bom que resolveu! 👍", ephemeral=True)
        await finalizar_resolvido(interaction, CHATGURU_ROLE_ID)


class ChatGuruThirdView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Não resolveu", style=discord.ButtonStyle.danger)
    async def no_resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "aguardou_5min", "nao_resolveu")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        await thread.send(
            "🔍 Confirma aqui para mim: existe uma mensagem de erro na página atual? "
            "Passe o mouse por cima do ponto de exclamação e me diga qual o número aparece "
            "logo antes da mensagem "
            "(ex: '131049 - mensagem...')",
            view=ChatGuruFourthView(self.original_user_id)
        )

    @discord.ui.button(label="Resolveu", style=discord.ButtonStyle.success)
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "aguardou_5min", "resolveu")
        await disable_view(interaction, self)
        await interaction.response.send_message("Ótimo, que bom que resolveu!", ephemeral=True)
        await finalizar_resolvido(interaction, CHATGURU_ROLE_ID)


class ChatGuruFourthView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="não existe", style=discord.ButtonStyle.danger)
    async def no_exist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "mensagem_erro_existe", "nao")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        guild = interaction.guild

        role = None
        try:
            if guild:
                role = guild.get_role(CHATGURU_ROLE_ID)
        except Exception:
            role = None

        if role:
            try:
                await thread.send("❗ O usuário informou que não há mensagem de erro visível. "
                "Chamando equipe ChatGuru:")
                await thread.send(role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
            except Exception as e:
                print(f"[WARN] não conseguiu mencionar cargo ChatGuru: {e}")
                await thread.send("❗ O usuário informou que não há mensagem "
                "de erro visível. Por favor, "
                "equipe ChatGuru, verifiquem este chamado.")
        else:
            await thread.send("❗ Cargo ChatGuru não encontrado. Verifiquem manualmente este chamado.")

    @discord.ui.button(label="existe", style=discord.ButtonStyle.primary)
    async def exist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "mensagem_erro_existe", "sim")
        await disable_view(interaction, self)
        await interaction.response.send_modal(ErrorNumberModal(self.original_user_id))


# ----- ClickupSlowView -----
class ClickupSlowView(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Preciso muito de ajuda", style=discord.ButtonStyle.danger)
    async def urgent_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "solicitou_suporte_urgente_clickup", "sim")
        await disable_view(interaction, self)
        await interaction.response.defer()
        thread = interaction.channel
        guild = interaction.guild

        role = None
        try:
            if guild:
                role = guild.get_role(CLICKUP_SUPPORT_ROLE_ID)
        except Exception:
            role = None

        if role:
            try:
                await thread.send("❗ Usuário solicitou suporte ClickUp (precisa muito de ajuda). Chamando equipe ClickUp:", allowed_mentions=discord.AllowedMentions(roles=True))
                await thread.send(role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
            except Exception as e:
                print(f"[WARN] falha ao mencionar Clickup support role: {e}")
                try:
                    await thread.send(f"❗ Chamando equipe ClickUp: <@&{CLICKUP_SUPPORT_ROLE_ID}>", allowed_mentions=discord.AllowedMentions(roles=True))
                except Exception:
                    await thread.send("❗ Chamando equipe ClickUp (não foi possível mencionar automaticamente).")
        else:
            try:
                await thread.send(f"❗ Chamando equipe ClickUp: <@&{CLICKUP_SUPPORT_ROLE_ID}>", allowed_mentions=discord.AllowedMentions(roles=True))
            except Exception:
                await thread.send("❗ Chamando equipe ClickUp (role não encontrada).")


class ProblemTypeViewClickup(discord.ui.View):
    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message("Apenas o solicitante pode interagir aqui.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🐢lentidão/travamento🐢", style=discord.ButtonStyle.primary)
    async def slow(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "lentidao_travamento")
        await disable_view(interaction, self)
        thread = interaction.channel

        expl_text = (
            "⚙️ Observação técnica: o ClickUp é hospedado "
            "nos EUA e, em períodos de maior latência, "
            "é comum apresentar lentidão ou travamentos pontuais. Algumas "
            "ações que ajudam:\n\n"
            "• Tente alternar entre a versão web e o app (Windows Store) —"
            "às vezes o app é mais estável.\n"
            "• Limpe cache / força recarga (Ctrl+F5) na versão web.\n"
            "• Verifique se alguma extensão do navegador pode estar interferindo.\n\n"
            "Se preferir que a equipe verifique, escolha uma das opções abaixo:"
        )

        await thread.send(expl_text, view=ClickupSlowView(self.original_user_id))

    @discord.ui.button(label="🌐página não carrega🌐", style=discord.ButtonStyle.success)
    async def page_not_load(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "pagina_nao_carrega")
        await disable_view(interaction, self)
        thread = interaction.channel
        await thread.send(
            "Você já tentou limpar o cache da página? Limpando o cachê o site pode voltar ao normal, pois é um reset de dados.",
            view=ClickupFirstView(self.original_user_id)
        )

    @discord.ui.button(label="🔐permissão/cadastro🔐", style=discord.ButtonStyle.primary)
    async def permission(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "permissao_cadastro")
        await disable_view(interaction, self)
        thread = interaction.channel
        guild = interaction.guild

        role = None
        try:
            if guild:
                role = guild.get_role(CLICKUP_SUPPORT_ROLE_ID)
                if not role:
                    cfg = get_config_for_guild(guild.id) or {}
                    cargo_role, _ = resolve_cargo_ti(guild, cfg)
                    role = cargo_role if cargo_role else None
        except Exception:
            role = None

        if role:
            try:
                await thread.send("🔐 O usuário informou problema de permissão/cadastro. Chamando equipe ClickUp:", allowed_mentions=discord.AllowedMentions(roles=True))
                await thread.send(role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
            except Exception as e:
                print(f"[WARN] falha ao mencionar role de permissão Clickup: {e}")
                await thread.send("🔐 O usuário informou problema de permissão/cadastro. Por favor, equipe ClickUp, verifiquem este chamado.")
        else:
            try:
                await thread.send(f"🔐 O usuário informou problema de permissão/cadastro. Por favor, equipe ClickUp, verifiquem este chamado. (Ping: <@&{CLICKUP_SUPPORT_ROLE_ID}>)", allowed_mentions=discord.AllowedMentions(roles=True))
            except Exception:
                await thread.send("🔐 O usuário informou problema de permissão/cadastro. Por favor, equipe ClickUp, verifiquem este chamado.")

        await interaction.response.send_message("Equipe foi acionada para permissões/cadastro.", ephemeral=True)

    @discord.ui.button(label="❌mensagem de erro❌", style=discord.ButtonStyle.danger)
    async def error_msg(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "mensagem_de_erro")
        await disable_view(interaction, self)
        thread = interaction.channel
        await thread.send(
            "Você já tentou limpar o cache da página? Limpando o cachê o site pode voltar ao normal, pois é um reset de dados.",
            view=ClickupFirstView(self.original_user_id)
        )

    @discord.ui.button(label="⚠️mensagem de aviso⚠️", style=discord.ButtonStyle.success)
    async def warning(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        update_payload_step(interaction.channel.id, "tipo_problema", "mensagem_de_aviso")
        await disable_view(interaction, self)
        thread = interaction.channel
        await thread.send(
            "Você já tentou limpar o cache da página? Limpando o cachê o site pode voltar ao normal, pois é um reset de dados.",
            view=ClickupFirstView(self.original_user_id)
        )


# ----- ServicesView (botões principais) -----
class ServicesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="💬ChatGuru💬", style=discord.ButtonStyle.success)
    async def chatguru(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ServiceModal("ChatGuru"))

    @discord.ui.button(label="⚖️Whom⚖️", style=discord.ButtonStyle.primary)
    async def whom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ServiceModal("Whom"))

    @discord.ui.button(label="⚙️Clickup⚙️", style=discord.ButtonStyle.danger)
    async def clickup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ServiceModal("Clickup"))


async def handle_service_modal_submit(interaction: discord.Interaction, sistema: str, descricao: str):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    channel = interaction.channel
    user = interaction.user

    if guild is None or channel is None:
        await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
        return

    cfg = get_config_for_guild(guild.id) or {}
    thread_name = f"{sistema} - {user.display_name}"

    try:
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES
        )
    except Exception as e:
        await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
        print(e)
        return

    await safe_join_thread(thread)
    try:
        await thread.add_user(user)
    except Exception:
        pass

    embed = discord.Embed(
        title=f"🧩 Chamado - {sistema}",
        description=(
            f"👤 **Usuário:** {user.mention}\n\n"
            f"📝 **Descrição inicial do problema:**\n"
            f"{descricao}\n\n"
            f"🔎 **Selecione abaixo o tipo que melhor descreve a situação:**"
        ),
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now()
    )

    embed.add_field(name="🐢 Lentidão / Travamento", value="O sistema está lento, congelando ou demorando para responder.\n\n", inline=False)
    embed.add_field(name="🌐 Página não carrega", value="A página fica em branco, carregando infinitamente ou retorna erro de acesso.\n\n", inline=False)
    embed.add_field(name="🔐 Permissão / Cadastro", value="Problemas de acesso, bloqueio de usuário, falta de permissão ou necessidade de cadastro.\n\n", inline=False)
    embed.add_field(name="❌ Mensagem de erro", value="Apareceu uma mensagem de erro específica (ex: código, alerta vermelho, falha crítica).\n\n", inline=False)
    embed.add_field(name="⚠️ Mensagem de aviso", value="Apareceu um alerta ou aviso no sistema, mas sem bloquear totalmente o uso.\n\n", inline=False)
    embed.set_footer(text="Após selecionar uma opção, o diagnóstico automático será iniciado.")

    try:
        await thread.send(embed=embed)
    except Exception as e:
        print(f"[WARN] erro ao enviar embed na thread: {e}")

    payload = {
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
        "thread_id": getattr(thread, "id", None),
        "thread_name": getattr(thread, "name", None),
        "thread_url": getattr(thread, "jump_url", None),
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "cargo_ti_id": cfg.get("cargo_ti"),
        "steps": {}  # inicializado vazio; será preenchido conforme o usuário clica
    }

    try:
        if thread and getattr(thread, "id", None):
            PENDING_PAYLOADS[thread.id] = payload
            print(f"[DEBUG] payload salvo aguardando setor do colaborador: thread {thread.id}")
    except Exception as e:
        print(f"[WARN] não conseguiu salvar payload pendente: {e}")

    if sistema.lower() == "chatguru":
        try:
            await thread.send("Qual o tipo de problema? Escolha uma opção abaixo:", view=ProblemTypeView(user.id))
        except Exception as e:
            print(f"[WARN] não conseguiu enviar pergunta inicial do ChatGuru: {e}")

    elif sistema.lower() == "whom":
        try:
            await thread.send("Qual o tipo de problema? Escolha uma opção abaixo:", view=ProblemTypeViewWhom(user.id))
        except Exception as e:
            print(f"[WARN] não conseguiu enviar pergunta inicial do Whom: {e}")

    elif sistema.lower() == "clickup":
        try:
            await thread.send("Qual o tipo de problema? Escolha uma opção abaixo:", view=ProblemTypeViewClickup(user.id))
        except Exception as e:
            print(f"[WARN] não conseguiu enviar pergunta inicial do ClickUp: {e}")

    else:
        cargo_role, _ = resolve_cargo_ti(guild, cfg)
        if cargo_role:
            try:
                await thread.send(cargo_role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
            except Exception:
                pass


@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")

    for guild_id, cfg in SERVIDORES.items():
        guild = bot.get_guild(guild_id)
        if not guild:
            print(f"[WARN] Bot não está no guild {guild_id}")
            continue

        canal = guild.get_channel(CANAL_ID)
        if not canal:
            print(f"[WARN] Canal {CANAL_ID} não encontrado em guild {guild.name} ({guild_id})")
            continue

        try:
            embed = discord.Embed(
                title="🛠️ Central de Suporte Técnico - Sistemas",
                description=(
                    "Bem-vindo à Central de Suporte.\n\n"
                    "📌 **Como funciona o atendimento:**\n"
                    "• Selecione abaixo o sistema que está apresentando problema.\n"
                    "• O bot fará algumas perguntas rápidas para tentar resolver automaticamente.\n"
                    "• Caso não seja resolvido, a equipe responsável será acionada.\n\n"
                    "⚠️ **Importante:**\n"
                    "• Descreva o problema com o máximo de detalhes possível.\n"
                    "• Envie prints ou mensagens de erro, se houver.\n"
                    "• Informe quando o problema começou.\n\n"
                    "👇 Escolha o sistema correspondente abaixo:"
                ),
                color=discord.Color.blurple()
            )

            await canal.send(embed=embed, view=ServicesView())
            print(f"[DEBUG] Mensagem com botões enviada em {guild.name} -> canal {canal.name}")
        except Exception as e:
            print(f"[ERROR] Erro ao enviar mensagem inicial em {guild.name}: {e}")


if __name__ == "__main__":
    if DISCORD_TOKEN == "SEU_TOKEN_AQUI":
        print("ATENÇÃO: defina a variável TOKEN com o token do bot.")
    else:
        bot.run(DISCORD_TOKEN)