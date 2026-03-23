"""
main.py — Entry point do Caveira Unificado.

Como adicionar um novo módulo:
  1. Crie modules/seu_modulo.py com uma função setup(bot) e uma View de entrada.
  2. Importe aqui: from modules import seu_modulo
  3. Registre em setup_modules(): seu_modulo.setup(bot)
  4. Adicione um botão em MainMenuView chamando a View de entrada do módulo.
  5. Atualize o embed de boas-vindas em _build_menu_embed().
  6. Registre a view persistente em on_ready(): bot.add_view(seu_modulo.SuaView())
"""

import discord
from discord.ext import commands

import config
from modules import ti as ti_module
from modules import contato as contato_module
from modules import sistemas as sistemas_module

# ─────────────────────────────────────────────────────────────────────────────
#  Bot
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# ─────────────────────────────────────────────────────────────────────────────
#  Menu principal
# ─────────────────────────────────────────────────────────────────────────────

class MainMenuView(discord.ui.View):
    """
    View fixada no canal unificado.
    Ordem dos botões: Sistemas | Equipamentos | Recuperar Contato | Reembolso
    """

    def __init__(self):
        super().__init__(timeout=None)

    # ── Sistemas ──────────────────────────────────────────────────────────────
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

    # ── Equipamentos ──────────────────────────────────────────────────────────
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

    # ── Recuperar Contato ─────────────────────────────────────────────────────
    @discord.ui.button(
        label="📞 Recuperar Contato",
        style=discord.ButtonStyle.success,
        custom_id="menu_btn_contato",
        row=0,
    )
    async def contato_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(contato_module.ContatoModal())

    # ── Solicitação de Reembolso ──────────────────────────────────────────────
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
    """Cria o embed do menu principal. Atualize ao adicionar módulos."""
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
    """
    Registra todos os comandos dos módulos no bot.
    Chamado UMA vez em __main__, antes de bot.run().
    NÃO inicie tasks aqui — faça isso no on_ready().
    """
    ti_module.setup(bot)
    contato_module.setup(bot)
    sistemas_module.setup(bot)


# ─────────────────────────────────────────────────────────────────────────────
#  Eventos
# ─────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user} (ID: {bot.user.id})")

    # Registra views persistentes (botões funcionam após reinício)
    bot.add_view(MainMenuView())
    bot.add_view(ti_module.UrgenciaView())
    bot.add_view(sistemas_module.ServicesView())

    # Inicia tasks que precisam do event loop (só aqui, nunca em setup_modules)
    contato_module.start_tasks()

    # Envia (ou atualiza) o menu em cada guild configurada
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

        # Remove mensagens antigas do bot
        try:
            async for msg in canal.history(limit=50):
                if msg.author == bot.user:
                    await msg.delete()
        except Exception as e:
            print(f"[WARN] Erro ao limpar histórico em '{nome}': {e}")

        # Posta menu principal
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
    """Lista os comandos disponíveis para a equipe."""
    embed = discord.Embed(
        title="📖 Comandos disponíveis",
        description="Estes comandos são usados **dentro dos tópicos** abertos pelos colaboradores. Use-os para encerrar o atendimento corretamente.",
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="🖥️ `!logs`",
        value=(
            "Usado dentro de um tópico de **Equipamentos/T.I.**\n"
            "Remove o colaborador do tópico, abre um formulário para você preencher "
            "(empresa, tipo do problema e nível real), gera um arquivo de log com todo "
            "o histórico da conversa e envia os dados para o sistema. O tópico é apagado automaticamente ao final."
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

    # Apaga qualquer mensagem enviada no canal do menu
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