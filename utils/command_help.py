from __future__ import annotations

import discord
from discord.ext import commands


def build_commands_help_embed(intro: str | None = None) -> discord.Embed:
    description = (
        "Estes comandos são usados **dentro dos tópicos** abertos pelos colaboradores. "
        "Use-os para encerrar o atendimento corretamente."
    )
    if intro:
        description = f"{intro}\n\n{description}"

    embed = discord.Embed(
        title="📖 Comandos disponíveis",
        description=description,
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
            "Indica que a busca foi concluída, envia a mensagem de conclusão para o colaborador, "
            "remove o responsável pela busca do tópico e agenda o fechamento automático após 8 horas."
        ),
        inline=False,
    )
    embed.set_footer(text="Todos os comandos só funcionam dentro dos tópicos correspondentes.")
    return embed


async def reply_commands_help(
    ctx: commands.Context,
    intro: str | None = None,
) -> None:
    await ctx.reply(
        embed=build_commands_help_embed(intro),
        mention_author=False,
    )
