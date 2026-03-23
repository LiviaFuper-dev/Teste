"""
modules/sistemas/__init__.py — Ponto de entrada público do pacote Sistemas.

É o ÚNICO arquivo que main.py e outros módulos externos precisam conhecer.
Expõe: ServicesView, setup()

Para adicionar um novo sistema:
  1. Crie modules/sistemas/_novo.py com a View/função necessária.
  2. Adicione entrada em SISTEMAS_CONFIG em _engine.py (se usar fluxo genérico).
  3. Importe e adicione um botão em ServicesView abaixo.
  4. Se tiver escalada especial, registre em _escalada_final() abaixo.
"""

import asyncio
import datetime

import discord
from discord.ext import commands

import config
from ._engine import SISTEMAS_CONFIG, PENDING_PAYLOADS, ProblemTypeView, _ping_role
from ._chatguru import ChatGuruFourthView
from ._whom import WhomWarningView
from ._email import _criar_thread_email
from ._falepaco import _abrir_falepaco_menu
from ._robos import _abrir_robos_menu
from ._3c import _abrir_3cplus
from ._command import setup as _setup_command


# ── _escalada_final ───────────────────────────────────────────────────────────

async def _escalada_final(
    interaction: discord.Interaction, sistema: str, cfg: dict
) -> None:
    """Chamada pela DiagnosticoView quando todos os passos genéricos falharam."""
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


# ── ServiceModal ──────────────────────────────────────────────────────────────

class ServiceModal(discord.ui.Modal):
    def __init__(self, sistema: str):
        super().__init__(title=f"Suporte - {sistema}")
        self.sistema = sistema
        self.descricao = discord.ui.TextInput(
            label=f"Qual problema você está tendo no {sistema}?",
            style=discord.TextStyle.paragraph,
            placeholder="Descreva o problema com o máximo de detalhes possível...",
            required=True,
            max_length=1000,
        )
        self.add_item(self.descricao)

    async def on_submit(self, interaction: discord.Interaction):
        await _handle_modal_submit(interaction, self.sistema, self.descricao.value)


async def _handle_modal_submit(
    interaction: discord.Interaction,
    sistema: str,
    descricao: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    channel = interaction.channel
    user = interaction.user

    if not guild or not channel:
        await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
        return

    try:
        thread = await channel.create_thread(
            name=f"3 - {sistema} - {user.display_name}",
            type=discord.ChannelType.private_thread,
            auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
        )
    except Exception as e:
        await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
        print(f"[SISTEMAS] Erro ao criar thread: {e}")
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

    await interaction.followup.send("Tópico criado! Acesse-o para continuar.", ephemeral=True)


# ── ServicesView ──────────────────────────────────────────────────────────────
# Único ponto de contato com main.py.
# Recebe menu_interaction (o clique em "⚙️ Sistemas" no menu principal)
# para apagar o próprio ephemeral após o usuário fazer uma escolha.

class ServicesView(discord.ui.View):
    """Exibida como ephemeral quando o usuário clica em Sistemas no menu."""

    def __init__(self, menu_interaction: discord.Interaction = None):
        super().__init__(timeout=None)
        self.menu_interaction = menu_interaction

    async def _fechar_ephemeral(self) -> None:
        """Apaga o ServicesView ephemeral após 3s."""
        if self.menu_interaction:
            await asyncio.sleep(3)
            try:
                await self.menu_interaction.delete_original_response()
            except Exception:
                pass

    @discord.ui.button(label="💬ChatGuru💬", style=discord.ButtonStyle.success, custom_id="sistemas_btn_chatguru")
    async def chatguru(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ServiceModal("ChatGuru"))
        await self._fechar_ephemeral()

    @discord.ui.button(label="⚖️Whom⚖️", style=discord.ButtonStyle.primary, custom_id="sistemas_btn_whom")
    async def whom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ServiceModal("Whom"))
        await self._fechar_ephemeral()

    @discord.ui.button(label="⚙️Clickup⚙️", style=discord.ButtonStyle.danger, custom_id="sistemas_btn_clickup")
    async def clickup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ServiceModal("Clickup"))
        await self._fechar_ephemeral()

    @discord.ui.button(label="📧 E-mail 📧", style=discord.ButtonStyle.secondary, custom_id="sistemas_btn_email")
    async def email(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _criar_thread_email(interaction)
        await self._fechar_ephemeral()

    @discord.ui.button(label="📞 Falepaco 📞", style=discord.ButtonStyle.primary, custom_id="sistemas_btn_falepaco")
    async def falepaco(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _abrir_falepaco_menu(interaction)
        await self._fechar_ephemeral()

    @discord.ui.button(label="☎️ 3C+ ☎️", style=discord.ButtonStyle.success, custom_id="sistemas_btn_3cplus")
    async def tres_c_plus(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _abrir_3cplus(interaction)
        await self._fechar_ephemeral()

    @discord.ui.button(label="🤖 Robôs/Automações 🤖", style=discord.ButtonStyle.secondary, custom_id="sistemas_btn_robos")
    async def robos(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _abrir_robos_menu(interaction)
        await self._fechar_ephemeral()

    @discord.ui.button(label="☎️ 3C+ ☎️", style=discord.ButtonStyle.success, custom_id="sistemas_btn_3cplus")
    async def tres_c_plus(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user

        if not guild or not channel:
            await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
            return

        try:
            thread = await channel.create_thread(
                name=f"3 - 3c+ - {user.display_name}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
            )
        except Exception as e:
            await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
            print(f"[SISTEMAS] Erro ao criar thread 3c+: {e}")
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

        PENDING_PAYLOADS[thread.id] = {
            "event": "topic_created",
            "system": "3c+",
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

        role = guild.get_role(1484627219518062864)
        try:
            await thread.send(
                f"Olá, {user.mention}! Tudo bem? 😊\n\n"
                "Recebemos seu chamado sobre o **3c+**.\n"
                "Por favor, descreva aqui o que está acontecendo e nossa equipe entrará em contato em breve.",
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            if role:
                await thread.send(role.mention, allowed_mentions=discord.AllowedMentions(roles=True))
        except Exception as e:
            print(f"[SISTEMAS] Erro ao enviar mensagem na thread 3c+: {e}")

        await interaction.followup.send("Tópico criado! Acesse-o para continuar.", ephemeral=True)
        await self._fechar_ephemeral()


# ── setup ─────────────────────────────────────────────────────────────────────

def setup(bot: commands.Bot) -> None:
    """Registra todos os comandos do módulo sistemas no bot."""
    _setup_command(bot)