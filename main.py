"""
main.py — Entry point do Caveira Unificado.

Auto-fechamento por inatividade:
  - Tópicos "1 - *" (sistemas) → movidos para #chamados-pendentes após 24h sem interação
  - Tópicos "2 - *" (equipamentos/TI) → movidos para #chamados-pendentes após 24h sem interação
  - Tópicos "3 - *" (recuperar contato) → movidos para #chamados-pendentes após 24h sem interação
  - Thread IDs já processados são salvos em data/thread_auto_actions.json
    para não serem processados novamente após reinício do bot.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks

import config
from modules import contato as contato_module
from modules import sistemas as sistemas_module
from modules import ti as ti_module
from utils.pending_chamados import PendingChamadoView, chamado_pendente_existe, criar_card_pendente

# ─────────────────────────────────────────────────────────────────────────────
#  Bot
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

PENDING_INACTIVITY_HOURS = 24
_HANDLED_FILE = Path("data/thread_auto_actions.json")
_HANDLED_FILE.parent.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Persistência de tópicos já processados automaticamente
# ─────────────────────────────────────────────────────────────────────────────

def _load_handled() -> set[int]:
    if not _HANDLED_FILE.exists():
        return set()
    try:
        return {int(x) for x in json.loads(_HANDLED_FILE.read_text(encoding="utf-8"))}
    except Exception:
        return set()


def _save_handled(ids: set[int]) -> None:
    try:
        _HANDLED_FILE.write_text(
            json.dumps(sorted(ids), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Monitor de inatividade
# ─────────────────────────────────────────────────────────────────────────────

async def _ultima_atividade(thread: discord.Thread) -> datetime:
    """Retorna o datetime da última mensagem do tópico (ou created_at se vazio)."""
    try:
        async for msg in thread.history(limit=1):
            ts = msg.created_at
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    ts = thread.created_at or datetime.now(timezone.utc)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def _ultima_atividade_humana(thread: discord.Thread) -> datetime:
    """Retorna o datetime da última mensagem de um humano (ignora bots)."""
    try:
        async for msg in thread.history(limit=50):
            if not msg.author.bot:
                ts = msg.created_at
                return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    ts = thread.created_at or datetime.now(timezone.utc)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)

# Retorna a última interação registrada no payload de Sistemas.
def _payload_last_interaction(thread_id: int) -> datetime | None:
    try:
        from modules.sistemas._engine import PENDING_PAYLOADS
        raw = PENDING_PAYLOADS.get(thread_id, {}).get("last_interaction_at")
        if not raw:
            return None
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except Exception:
        return None


@tasks.loop(minutes=10)
async def auto_fechar_inativos():
    """
    Varre tópicos ativos e move chamados abandonados para #chamados-pendentes.
    Os comandos manuais (!sistema, !logs, !contato) continuam finalizando na hora.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=PENDING_INACTIVITY_HOURS)
    handled = _load_handled()
    changed = False

    for guild_id in config.SERVIDORES.keys():
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue

        threads_by_id = {}
        try:
            for thread in guild.threads:
                threads_by_id[thread.id] = thread
        except Exception:
            pass

        canal_unificado_id = config.SERVIDORES.get(guild_id, {}).get("canal_unificado")
        parent = guild.get_channel(canal_unificado_id) if canal_unificado_id else None
        if parent and hasattr(parent, "archived_threads"):
            for private in (True, False):
                try:
                    async for thread in parent.archived_threads(limit=None, private=private):
                        threads_by_id[thread.id] = thread
                except Exception as e:
                    print(f"[AUTO-CLOSE] Erro ao buscar threads arquivadas em {guild.id}: {e}")

        threads = list(threads_by_id.values())

        for thread in threads:
            try:
                if thread.id in handled:
                    continue

                name = thread.name.strip()
                if not (name.startswith("1 -") or name.startswith("2 -") or name.startswith("3 -")):
                    continue

                if thread.archived:
                    try:
                        await thread.edit(archived=False)
                    except Exception as e:
                        print(f"[AUTO-CLOSE] Nao foi possivel reabrir thread arquivada {thread.id}: {e}")
                        continue

                if name.startswith("3 -"):
                    from modules.contato import get_thread_activity, is_thread_finalizada
                    if is_thread_finalizada(thread.id):
                        continue
                    ultima = await _ultima_atividade_humana(thread)
                    activity_ts = get_thread_activity(thread.id)
                    if activity_ts and activity_ts > ultima:
                        ultima = activity_ts
                else:
                    ultima = await _ultima_atividade(thread)
                    if name.startswith("1 -"):
                        payload_ts = _payload_last_interaction(thread.id)
                        if payload_ts and payload_ts > ultima:
                            ultima = payload_ts
                if ultima > cutoff:
                    continue

                print(f"[AUTO-CLOSE] Inativo: '{thread.name}' ({thread.id})")

                processed = False
                if name.startswith("1 -"):
                    processed = await _auto_pendenciar_sistemas(thread, guild)
                elif name.startswith("2 -"):
                    processed = await _auto_pendenciar_ti(thread, guild)
                elif name.startswith("3 -"):
                    processed = await _auto_pendenciar_contato(thread, guild)

                if processed:
                    handled.add(thread.id)
                    changed = True

            except discord.NotFound:
                handled.add(thread.id)
                changed = True
            except discord.Forbidden:
                print(f"[AUTO-CLOSE] Sem permissão: {thread.id}")
            except Exception as e:
                print(f"[AUTO-CLOSE] Erro em {thread.id}: {e}")

    if changed:
        _save_handled(handled)


async def _guess_thread_user(
    thread: discord.Thread,
    guild: discord.Guild,
    allowed_role_ids: set[int] | None = None,
) -> discord.Member | None:
    try:
        await thread.fetch_members()
    except Exception:
        pass

    fallback = None
    for tm in thread.members:
        try:
            member = guild.get_member(tm.id) or await guild.fetch_member(tm.id)
        except Exception:
            continue
        if member.bot:
            continue
        if fallback is None:
            fallback = member
        if allowed_role_ids and not any(role.id in allowed_role_ids for role in member.roles):
            return member
    return fallback


async def _archive_original_thread(thread: discord.Thread) -> None:
    try:
        await thread.edit(archived=True, locked=True)
    except TypeError:
        await thread.edit(archived=True)
    except Exception as e:
        print(f"[PENDENTES] Erro ao arquivar '{thread.name}': {e}")


async def _auto_pendenciar_sistemas(thread: discord.Thread, guild: discord.Guild) -> bool:
    """Move um chamado de sistemas inativo para o painel de pendentes."""
    from modules.sistemas._engine import _allowed_roles, pop_payload
    from utils.logs import enviar_log_conversa

    payload = pop_payload(thread.id)
    allowed = _allowed_roles(guild.id)
    member = None
    if payload and payload.get("user_id"):
        try:
            member = guild.get_member(int(payload["user_id"])) or await guild.fetch_member(int(payload["user_id"]))
        except Exception:
            member = None
    if member is None:
        member = await _guess_thread_user(thread, guild, allowed)

    parts = thread.name.split(" - ")
    sistema = payload.get("system") if payload else (parts[1] if len(parts) > 1 else "Sistemas")
    motivo = payload.get("description") if payload else "Chamado ficou 24h sem interação."
    if payload and sistema in {"E-mail", "Google Drive"}:
        steps = payload.get("steps", {})
        resumo = ["Chamado ficou 24h sem interação."]
        if steps.get("dominio_detectado"):
            resumo.append(f"E-mail selecionado: {steps['dominio_detectado']}")
        if steps.get("email_usuario"):
            resumo.append(f"E-mail informado: {steps['email_usuario']}")
        if steps.get("problema"):
            resumo.append(f"Problema relatado: {steps['problema']}")
        motivo = "\n".join(resumo)
    user_id = int(payload.get("user_id")) if payload and payload.get("user_id") else (member.id if member else 0)
    user_name = payload.get("user_name") if payload else (member.display_name if member else "Usuario nao identificado")

    if not chamado_pendente_existe(thread.id):
        await thread.send(
            "⚠️ Este chamado ficou 24 horas sem interação e foi movido para o painel de chamados pendentes."
        )

    canal_logs_id = config.SERVIDORES.get(guild.id, {}).get("canal_logs")
    log_url = None
    if canal_logs_id:
        log_url = await enviar_log_conversa(
            thread,
            guild,
            canal_logs_id,
            prefixo_log="[SISTEMAS]",
            header_extra="=== Chamado pendente (Sistemas) ===\nMotivo: Inatividade 24h",
        )

    ok = await criar_card_pendente(
        thread,
        guild,
        kind="sistemas",
        tipo_label="Sistemas",
        sistema=sistema,
        user_id=user_id,
        user_name=user_name,
        motivo=motivo or "Chamado ficou 24h sem interação.",
        log_url=log_url,
        payload=payload,
    )
    if not ok:
        return False

    await _archive_original_thread(thread)
    print(f"[PENDENTES] Sistemas pendente: '{thread.name}'")
    return True


async def _auto_pendenciar_ti(thread: discord.Thread, guild: discord.Guild) -> bool:
    """Move um chamado de TI inativo para o painel de pendentes."""
    from utils.logs import enviar_log_conversa

    cargo_ti = ti_module._get_cargo_ti(guild, guild.id)
    allowed = {cargo_ti.id} if cargo_ti else set()
    member = await _guess_thread_user(thread, guild, allowed)
    motivo = ti_module.pop_thread_motivo(thread.id) or "Chamado de TI ficou 24h sem interação."

    if not chamado_pendente_existe(thread.id):
        await thread.send(
            "⚠️ Este chamado ficou 24 horas sem interação e foi movido para o painel de chamados pendentes."
        )

    canal_logs_id = config.SERVIDORES.get(guild.id, {}).get("canal_logs")
    log_url = None
    if canal_logs_id:
        log_url = await enviar_log_conversa(
            thread,
            guild,
            canal_logs_id,
            prefixo_log="[TI]",
            header_extra="=== Chamado pendente (TI) ===\nMotivo: Inatividade 24h",
        )

    ok = await criar_card_pendente(
        thread,
        guild,
        kind="ti",
        tipo_label="Equipamentos/TI",
        sistema="Equipamentos",
        user_id=member.id if member else 0,
        user_name=member.display_name if member else "Usuario nao identificado",
        motivo=motivo,
        log_url=log_url,
    )
    if not ok:
        return False

    await _archive_original_thread(thread)
    print(f"[PENDENTES] TI pendente: '{thread.name}'")
    return True


async def _extract_contato_resumo(thread: discord.Thread) -> str:
    nome = None
    cpf = None
    try:
        async for msg in thread.history(oldest_first=True, limit=100):
            for line in msg.content.splitlines():
                clean = line.replace("*", "").strip()
                lower = clean.lower()
                if (
                    lower.startswith("nome:")
                    or lower.startswith("nome do contato:")
                ) and not nome:
                    nome = clean.split(":", 1)[1].strip()
                elif (
                    lower.startswith("cpf:")
                    or lower.startswith("cpf do contato:")
                ) and not cpf:
                    cpf = clean.split(":", 1)[1].strip()
    except Exception as e:
        print(f"[PENDENTES] Erro ao extrair dados de contato: {e}")

    partes = []
    if nome:
        partes.append(f"Nome do contato: {nome}")
    if cpf:
        partes.append(f"CPF do contato: {cpf}")
    if partes:
        return "\n".join(partes)
    return "Dados do contato nao encontrados no historico."


async def _auto_pendenciar_contato(thread: discord.Thread, guild: discord.Guild) -> bool:
    """Move um chamado de contato inativo para o painel de pendentes."""
    from modules.contato import _THREAD_ACTIVITY
    from utils.logs import enviar_log_conversa

    _THREAD_ACTIVITY.pop(thread.id, None)
    member = await _guess_thread_user(thread, guild, set())
    detalhes = await _extract_contato_resumo(thread)
    motivo = f"Chamado de recuperação de contato ficou 24h sem interação.\n{detalhes}"

    if not chamado_pendente_existe(thread.id):
        await thread.send(
            "⚠️ Este chamado ficou 24 horas sem interação e foi movido para o painel de chamados pendentes."
        )

    canal_logs_id = config.SERVIDORES.get(guild.id, {}).get("canal_logs")
    log_url = None
    if canal_logs_id:
        log_url = await enviar_log_conversa(
            thread,
            guild,
            canal_logs_id,
            prefixo_log="[CONTATO]",
            header_extra="=== Chamado pendente (Contato) ===\nMotivo: Inatividade 24h",
        )

    ok = await criar_card_pendente(
        thread,
        guild,
        kind="contato",
        tipo_label="Recuperar Contato",
        sistema="Contato",
        user_id=member.id if member else 0,
        user_name=member.display_name if member else "Usuario nao identificado",
        motivo=motivo,
        log_url=log_url,
    )
    if not ok:
        return False

    await _archive_original_thread(thread)
    print(f"[PENDENTES] Contato pendente: '{thread.name}'")
    return True


@auto_fechar_inativos.before_loop
async def before_auto_fechar():
    await bot.wait_until_ready()


# ─────────────────────────────────────────────────────────────────────────────
#  Menu principal
# ─────────────────────────────────────────────────────────────────────────────

class MainMenuView(discord.ui.View):
    def __init__(self, guild_id: int | None = None):
        super().__init__(timeout=None)
        modulos = {"sistemas", "ti", "contato", "reembolso"}
        if guild_id:
            srv = config.SERVIDORES.get(guild_id, {})
            modulos = set(srv.get("modulos_ativos", modulos))
        if "sistemas" not in modulos:
            self.remove_item(self.sistemas_button)
        if "ti" not in modulos:
            self.remove_item(self.ti_button)
        if "contato" not in modulos:
            self.remove_item(self.contato_button)
        if "reembolso" not in modulos:
            self.remove_item(self.reembolso_button)

    @discord.ui.button(
        label="⚙️ Sistemas",
        style=discord.ButtonStyle.primary,
        custom_id="menu_btn_sistemas",
        row=0,
    )
    async def sistemas_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Selecione o sistema com problema:",
            view=sistemas_module.ServicesView(menu_interaction=interaction),
            ephemeral=True,
        )

    @discord.ui.button(
        label="🖥️ Equipamentos",
        style=discord.ButtonStyle.danger,
        custom_id="menu_btn_ti",
        row=0,
    )
    async def ti_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🛠️ Suporte Técnico — Equipamentos",
            description=(
                "Selecione o nível que **melhor representa sua situação**:\n\n"
                "🟢 **Baixo** — pequenas dúvidas ou erros que não impedem o trabalho\n"
                "🔵 **Médio** — falhas que dificultam, mas não impedem o trabalho\n"
                "🔴 **Alto**  — situação crítica, não é possível trabalhar"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=ti_module.UrgenciaView(original_interaction=interaction),
            ephemeral=True,
        )

    @discord.ui.button(
        label="📞 Recuperar Contato",
        style=discord.ButtonStyle.success,
        custom_id="menu_btn_contato",
        row=0,
    )
    async def contato_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(contato_module.ContatoModal())

    @discord.ui.button(
        label="📋 Solicitação de Reembolso",
        style=discord.ButtonStyle.secondary,
        custom_id="menu_btn_reembolso",
        row=1,
    )
    async def reembolso_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        import asyncio
        await interaction.response.send_message(
            "📋 **Solicitação de Reembolso**\n\n"
            "Para solicitar o reembolso de um gasto realizado para o escritório, "
            "acesse o formulário pelo link abaixo e preencha com os detalhes do gasto e o comprovante:\n\n"
            f"🔗 {config.SDR_FORM_URL}",
            ephemeral=True,
        )
        await asyncio.sleep(10)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass


def _build_menu_embed(guild_id: int | None = None) -> discord.Embed:
    modulos = {"sistemas", "ti", "contato", "reembolso"}
    if guild_id:
        srv = config.SERVIDORES.get(guild_id, {})
        modulos = set(srv.get("modulos_ativos", modulos))
    embed = discord.Embed(
        title="Central de Atendimento",
        description=(
            "Olá! Seja bem-vindo à Central de Atendimento do escritório.\n"
            "Aqui você pode abrir chamados, solicitar suporte e resolver problemas do dia a dia.\n"
            "Selecione abaixo a opção que melhor descreve a sua necessidade.\n\u200b"
        ),
        color=discord.Color.blurple(),
    )
    if "sistemas" in modulos:
        embed.add_field(
            name="⚙️ Sistemas",
            value=(
                "Problemas com as ferramentas e sistemas que utilizamos no escritório.\n"
                "Esta opção cobre: **ChatGuru** (disparos e mensagens), **Whom** (peticionamento), "
                "**ClickUp** (tarefas e projetos), **E-mail** (contas do escritório), "
                "**Falepaco** (sistema interno) e **Robôs/Automações** (INSS, planilhas, IA e integrações).\n"
                "Ao clicar, o bot fará algumas perguntas rápidas para tentar resolver automaticamente. "
                "Se não resolver, a equipe responsável será acionada.\n\u200b"
            ),
            inline=False,
        )
    if "ti" in modulos:
        embed.add_field(
            name="🖥️ Equipamentos",
            value=(
                "Problemas relacionados ao seu computador ou periféricos.\n"
                "Use esta opção se o seu computador estiver lento, travando, não ligando, "
                "ou se algum equipamento estiver com defeito — como mouse, teclado, headset, "
                "monitor ou qualquer outro acessório de trabalho.\n\u200b"
            ),
            inline=False,
        )
    if "contato" in modulos:
        embed.add_field(
            name="📞 Recuperar Contato",
            value=(
                "Canal para solicitar a recuperação do telefone de um contato.\n"
                "Caso precise localizar o número de algum cliente ou pessoa de interesse "
                "e não esteja conseguindo encontrá-lo, utilize esta opção.\n\u200b"
            ),
            inline=False,
        )
    if "reembolso" in modulos:
        embed.add_field(
            name="📋 Solicitação de Reembolso",
            value=(
                "Gastou com algo necessário para o trabalho no escritório?\n"
                "Utilize esta opção para solicitar o reembolso do valor. "
                "Você será redirecionado para um formulário onde poderá descrever o gasto "
                "e anexar o comprovante.\n\u200b"
            ),
            inline=False,
        )
    embed.set_footer(text="Em caso de dúvidas, abra um chamado e nossa equipe te ajuda.")
    return embed


# ─────────────────────────────────────────────────────────────────────────────
#  Registro de módulos
# ─────────────────────────────────────────────────────────────────────────────

def setup_modules() -> None:
    ti_module.setup(bot)
    contato_module.setup(bot)
    sistemas_module.setup(bot)


# ─────────────────────────────────────────────────────────────────────────────
#  Eventos
# ─────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user} (ID: {bot.user.id})")

    bot.add_view(MainMenuView())
    bot.add_view(ti_module.UrgenciaView())
    bot.add_view(sistemas_module.ServicesView())
    bot.add_view(PendingChamadoView(bot))

    contato_module.start_tasks()

    if not auto_fechar_inativos.is_running():
        auto_fechar_inativos.start()
        print(f"✅ Monitor de pendentes iniciado (inatividade: {PENDING_INACTIVITY_HOURS}h).")

    for guild_id, srv_cfg in config.SERVIDORES.items():
        nome = srv_cfg.get("nome", str(guild_id))
        canal_id = srv_cfg.get("canal_unificado")

        if not canal_id:
            print(f"[WARN] '{nome}': canal_unificado não configurado.")
            continue

        canal = bot.get_channel(canal_id)
        if not canal:
            print(f"[WARN] Canal {canal_id} não encontrado em '{nome}'.")
            continue

        try:
            async for msg in canal.history(limit=50):
                if msg.author == bot.user:
                    await msg.delete()
        except Exception as e:
            print(f"[WARN] Erro ao limpar histórico em '{nome}': {e}")

        try:
            mensagem = await canal.send(
                embed=_build_menu_embed(guild_id),
                view=MainMenuView(guild_id),
                silent=True,
            )
            await mensagem.pin()
            print(f"[OK] Menu enviado em '{nome}' → #{canal.name}")
        except Exception as e:
            print(f"[ERROR] Erro ao enviar menu em '{nome}': {e}")


@bot.command(name="help")
async def help_cmd(ctx: commands.Context):
    embed = discord.Embed(
        title="📖 Comandos disponíveis",
        description=(
            "Estes comandos são usados **dentro dos tópicos** abertos pelos colaboradores. "
            "Use-os para encerrar o atendimento corretamente."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🖥️ `!logs`",
        value=(
            "Usado dentro de um tópico de **Equipamentos/T.I.**\n"
            "Remove o colaborador do tópico, abre um formulário para você preencher "
            "(empresa e nível real), gera um arquivo de log com todo o histórico "
            "e envia os dados para o sistema. O tópico é apagado automaticamente ao final."
        ),
        inline=False,
    )
    embed.add_field(
        name="⚙️ `!sistema`",
        value=(
            "Usado dentro de um tópico de **Sistemas** (ChatGuru, Whom, ClickUp, E-mail, Falepaco, 3c+, Robôs).\n"
            "Remove o colaborador do tópico, pede que você selecione o setor do colaborador "
            "e envia todas as informações do atendimento para o sistema. O tópico é apagado automaticamente ao final."
        ),
        inline=False,
    )
    embed.add_field(
        name="📞 `!contato`",
        value=(
            "Usado dentro de um tópico de **Recuperar Contato**.\n"
            "Indica que a busca foi concluída — envia a mensagem de conclusão para o colaborador, "
            "remove o responsável pela busca do tópico e agenda o fechamento automático após 12 horas."
        ),
        inline=False,
    )
    embed.set_footer(text="Todos os comandos só funcionam dentro dos tópicos correspondentes.")
    await ctx.reply(embed=embed, mention_author=False)


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    if message.guild:
        srv_cfg = config.SERVIDORES.get(message.guild.id, {})
        if message.channel.id == srv_cfg.get("canal_unificado"):
            try:
                await message.delete()
            except Exception:
                pass
            return

    await contato_module.on_message_contato(message)
    await bot.process_commands(message)


# ─────────────────────────────────────────────────────────────────────────────
#  Run
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN não definido no .env")
    else:
        setup_modules()
        bot.run(config.DISCORD_TOKEN)
