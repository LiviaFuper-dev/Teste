"""
main.py — Entry point do Caveira Unificado.

Auto-fechamento por inatividade:
  - Tópicos "1 - *" (sistemas) → fechados automaticamente após 24h sem mensagem
  - Tópicos "2 - *" (equipamentos/TI) → fechados automaticamente após 24h sem mensagem
  - Thread IDs já processados são salvos em data/thread_auto_actions.json
    para não serem fechados novamente após reinício do bot.
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

# ─────────────────────────────────────────────────────────────────────────────
#  Bot
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

AUTO_INACTIVITY_HOURS = 12
AUTO_CONTATO_INACTIVITY_HOURS = 72  # 3 dias
_HANDLED_FILE = Path("data/thread_auto_actions.json")
_HANDLED_FILE.parent.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Persistência de tópicos já fechados automaticamente
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
#  Loop de auto-fechamento
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


@tasks.loop(minutes=10)
async def auto_fechar_inativos():
    """
    A cada 1 minuto varre os tópicos ativos.
    - Prefixo "1 -" → sistemas  → envia SectorSelectView (mesmo fluxo do !sistema)
    - Prefixo "2 -" → TI        → envia LogsFormView (mesmo fluxo do !logs)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=AUTO_INACTIVITY_HOURS)
    handled = _load_handled()
    changed = False

    for guild_id in config.SERVIDORES.keys():
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue

        try:
            threads = guild.threads
        except Exception:
            threads = []

        for thread in threads:
            try:
                if thread.archived or thread.id in handled:
                    continue

                name = thread.name.strip()
                if not (name.startswith("1 -") or name.startswith("2 -") or name.startswith("3 -")):
                    continue

                if name.startswith("3 -"):
                    contato_cutoff = datetime.now(timezone.utc) - timedelta(hours=AUTO_CONTATO_INACTIVITY_HOURS)
                    ultima = await _ultima_atividade_humana(thread)
                    if ultima > contato_cutoff:
                        continue
                else:
                    ultima = await _ultima_atividade(thread)
                    if ultima > cutoff:
                        continue

                print(f"[AUTO-CLOSE] Inativo: '{thread.name}' ({thread.id})")

                if name.startswith("1 -"):
                    await _auto_fechar_sistemas(thread, guild)
                elif name.startswith("2 -"):
                    await ti_module.auto_fechar_ti(thread, guild)
                elif name.startswith("3 -"):
                    await _auto_fechar_contato(thread)

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


async def _auto_fechar_sistemas(thread: discord.Thread, guild: discord.Guild) -> None:
    """
    Tópico de sistemas inativo por 24h.
    Fecha automaticamente: envia payload pro N8N e deleta a thread.
    """
    from modules.sistemas._engine import _allowed_roles, _member_has_role, pop_payload, _send_to_n8n

    allowed = _allowed_roles(guild.id)

    # Remove membros sem cargo autorizado
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
        except Exception:
            pass

    await thread.send(
        "⏰ Este chamado atingiu **24 horas de inatividade**.\n"
        "Fechamento automático em andamento..."
    )

    # Pega o payload salvo e envia pro N8N
    payload = pop_payload(thread.id)
    if payload:
        payload["auto_close"] = True
        ok = await _send_to_n8n(payload)
        if ok:
            print(f"[AUTO-CLOSE] Payload enviado para N8N: '{thread.name}'")
        else:
            print(f"[AUTO-CLOSE] Falha ao enviar payload para N8N: '{thread.name}'")
    else:
        print(f"[AUTO-CLOSE] Nenhum payload pendente para '{thread.name}' ({thread.id})")

    # Arquiva a thread (não deleta)
    try:
        await thread.edit(archived=True, reason="Chamado de sistemas arquivado por inatividade.")
        print(f"[AUTO-CLOSE] Tópico '{thread.name}' arquivado.")
    except Exception as e:
        print(f"[AUTO-CLOSE] Erro ao arquivar '{thread.name}': {e}")


async def _auto_fechar_contato(thread: discord.Thread) -> None:
    """Tópico de contato inativo por 3 dias. Arquiva automaticamente."""
    from modules.contato import _THREAD_ACTIVITY
    _THREAD_ACTIVITY.pop(thread.id, None)

    await thread.send(
        "⏰ Este tópico atingiu **3 dias de inatividade** e será arquivado automaticamente."
    )
    try:
        await thread.edit(archived=True, reason="Tópico de contato arquivado por inatividade (3 dias).")
        print(f"[AUTO-CLOSE] Contato arquivado: '{thread.name}'")
    except Exception as e:
        print(f"[AUTO-CLOSE] Erro ao arquivar contato '{thread.name}': {e}")


@auto_fechar_inativos.before_loop
async def before_auto_fechar():
    await bot.wait_until_ready()


# ─────────────────────────────────────────────────────────────────────────────
#  Menu principal
# ─────────────────────────────────────────────────────────────────────────────

class MainMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

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


def _build_menu_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Central de Atendimento",
        description=(
            "Olá! Seja bem-vindo à Central de Atendimento do escritório.\n"
            "Aqui você pode abrir chamados, solicitar suporte e resolver problemas do dia a dia.\n"
            "Selecione abaixo a opção que melhor descreve a sua necessidade.\n\u200b"
        ),
        color=discord.Color.blurple(),
    )
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
    embed.add_field(
        name="📞 Recuperar Contato",
        value=(
            "Canal para solicitar a recuperação do telefone de um contato.\n"
            "Caso precise localizar o número de algum cliente ou pessoa de interesse "
            "e não esteja conseguindo encontrá-lo, utilize esta opção.\n\u200b"
        ),
        inline=False,
    )
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

    contato_module.start_tasks()

    if not auto_fechar_inativos.is_running():
        auto_fechar_inativos.start()
        print(f"✅ Auto-fechamento iniciado (inatividade: {AUTO_INACTIVITY_HOURS}h).")

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
                embed=_build_menu_embed(),
                view=MainMenuView(),
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