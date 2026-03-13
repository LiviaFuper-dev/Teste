"""
_falepaco.py — Sistema Falepaco.

Fluxo:
  Usuário clica "Falepaco" → ephemeral FalepacoView (some após escolha)
  → "Baixar Falepaco"  → thread "3 - Falepaco - {usuario}" + pinga cargo TI
  → "Esqueci a senha"  → ephemeral orientando a chamar o gestor (some em 15s)
"""

import asyncio

import discord

import config
from ._engine import _ping_role

_CARGO_FALEPACO_ID = 1415390806541598831


class FalepacoView(discord.ui.View):
    """Ephemeral com as duas opções do Falepaco."""

    def __init__(self, menu_interaction: discord.Interaction = None):
        super().__init__(timeout=None)
        self.menu_interaction = menu_interaction  # interação do botão Falepaco no ServicesView

    async def _fechar_este_ephemeral(self) -> None:
        """Apaga o FalepacoView ephemeral após 3s."""
        if self.menu_interaction:
            await asyncio.sleep(3)
            try:
                await self.menu_interaction.delete_original_response()
            except Exception:
                pass

    @discord.ui.button(label="Baixar Falepaco 📥", style=discord.ButtonStyle.primary)
    async def baixar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user

        if not guild or not channel:
            await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
            return

        try:
            thread = await channel.create_thread(
                name=f"3 - Falepaco - {user.display_name}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
            )
        except Exception as e:
            await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
            print(f"[SISTEMAS] Erro ao criar thread Falepaco: {e}")
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

        await _ping_role(
            thread, guild, _CARGO_FALEPACO_ID,
            f"👋 Olá {user.mention}, tudo bem?\n\n"
            "Vi que você deseja baixar o **Falepaco** 📥 e nossa equipe vai te ajudar com isso!\n\n"
            "Antes de começarmos, preciso que você confirme uma coisa:\n"
            "💻 **Você possui o aplicativo AnyDesk instalado no seu computador?**\n"
            "🔎 Para verificar:\n"
            "1️⃣ Aperte a **tecla Windows** no seu teclado.\n"
            "2️⃣ Na barra de pesquisa, digite **AnyDesk**.\n"
            "3️⃣ Abra o aplicativo.\n\n"
            "Assim que ele abrir, você verá um **número ao lado da mensagem \"Este dispositivo\"**.\n"
            "📨 **Envie esse número aqui no chat para nós.**\n\n"
            "❗ Caso você **não tenha o AnyDesk instalado**, pode baixar por aqui:\n"
            "🔗 https://anydesk.com/pt/downloads/windows\n\n"
            "Depois é só clicar em **\"Baixe Agora\"** ⬇️\n\n"
            "👨‍💻 Enquanto isso, já estou chamando nossa equipe para te ajudar no restante do processo.\n"
            "Obrigado! 🙌",
        )

        await self._fechar_este_ephemeral()

    @discord.ui.button(label="Esqueci a senha 🔑", style=discord.ButtonStyle.secondary)
    async def esqueci_senha(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Oi, tudo bem? 😊 Que pena que você esqueceu sua senha!\n\n"
            "🔑 Para resetar a senha do **Falepaco** você precisa falar diretamente com o "
            "seu **gestor**, tá bem? Ele é o responsável por fazer esse ajuste pra você.\n\n"
            "📌 Qualquer outra dúvida, pode me chamar aqui que eu apareço! 🚀",
            ephemeral=True,
        )
        await self._fechar_este_ephemeral()

        # Apaga a mensagem de senha após 15s
        await asyncio.sleep(15)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass


async def _abrir_falepaco_menu(interaction: discord.Interaction) -> None:
    """Envia o ephemeral com FalepacoView. A interaction aqui É a do botão Falepaco."""
    await interaction.response.send_message(
        "Selecione uma opção do **Falepaco**:",
        view=FalepacoView(menu_interaction=interaction),
        ephemeral=True,
    )