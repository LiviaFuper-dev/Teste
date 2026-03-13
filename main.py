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

bot = commands.Bot(command_prefix="!", intents=intents)


# ─────────────────────────────────────────────────────────────────────────────
#  Menu principal
# ─────────────────────────────────────────────────────────────────────────────

class MainMenuView(discord.ui.View):
    """
    View fixada no canal unificado.
    Cada botão abre o fluxo do módulo correspondente via ephemeral.
    """

    def __init__(self):
        super().__init__(timeout=None)
        # Botão de link (SDR) não aceita decorator — vai no __init__
        self.add_item(discord.ui.Button(
            label="📋 Solicitação de Reembolso 📋",
            style=discord.ButtonStyle.link,
            url=config.SDR_FORM_URL,
            row=1,
        ))

    # ── T.I. ─────────────────────────────────────────────────────────────────
    @discord.ui.button(
        label="🖥️ T.I.",
        style=discord.ButtonStyle.danger,
        custom_id="menu_btn_ti",
        row=0,
    )
    async def ti_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🛠️ Suporte Técnico — T.I.",
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

    # ── Recuperar Telefone ────────────────────────────────────────────────────
    @discord.ui.button(
        label="Recuperar Telefone 📞",
        style=discord.ButtonStyle.success,
        custom_id="menu_btn_contato",
        row=0,
    )
    async def contato_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(contato_module.ContatoModal())

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


def _build_menu_embed() -> discord.Embed:
    """Cria o embed do menu principal. Atualize ao adicionar módulos."""
    return discord.Embed(
        title="📋 Central de Atendimento",
        description=(
            "Bem-vindo! Selecione abaixo a área correspondente à sua necessidade.\n\n"
            "🖥️ **T.I.** — Problemas técnicos com hardware, rede ou equipamentos\n"
            "📞 **Recuperar Telefone** — Recuperação de telefone de contato\n"
            "⚙️ **Sistemas** — ChatGuru, Whom, ClickUp, E-mail\n"
            "📋 **Solicitação de Reembolso** — Formulário de reembolso\n"
        ),
        color=discord.Color.blurple(),
    )


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