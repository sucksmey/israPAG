import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
import re
from dotenv import load_dotenv

# --- CONFIGURAÇÕES ---
load_dotenv()
BOT_TOKEN = os.getenv('DISCORD_TOKEN')

# IDs do Servidor e Canais
GUILD_ID = 897650833888534588
LOG_CHANNEL_ID = 1382340441579720846
SALARY_CHANNEL_ID = 1385371013226827986

# ID do bot que envia os logs de compra
LOG_BOT_ID = 1379083772766720000

# --- CONFIGURAÇÕES DE SALÁRIO ---
COMISSAO_POR_VENDA_ROBUX = 10
COMISSAO_POR_VENDA_BRL = 0.34
META_VENDAS = 2942
META_BRL = 1000.00

# --- CONFIGURAÇÃO DO BANCO DE DADOS ---
DB_PATH = '/data/sales_data.db'

def setup_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL UNIQUE,
            attendant_id INTEGER NOT NULL,
            attendant_name TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

# --- BOTÕES DE CORREÇÃO ---
class CorrectionView(discord.ui.View):
    def __init__(self, original_message_id, new_attendant):
        super().__init__(timeout=300)
        self.original_message_id = original_message_id
        self.new_attendant = new_attendant

    @discord.ui.button(label="Sim, Corrigir", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE sales SET attendant_id = ?, attendant_name = ? WHERE message_id = ?",
            (self.new_attendant.id, self.new_attendant.name, self.original_message_id)
        )
        conn.commit()
        conn.close()
        await interaction.response.send_message(f"✅ Atendente da venda corrigido para {self.new_attendant.mention}!", ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Não, Cancelar", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("❌ Correção cancelada.", ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

# --- CLASSE PRINCIPAL DO BOT ---
class IsraBuyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print(f"Comandos slash sincronizados para o servidor {GUILD_ID}.")

    async def on_ready(self):
        print(f'Bot conectado como {self.user}')
        print('Iniciando varredura do histórico de logs com o ID do autor corrigido...')
        await self.scan_history()

    async def scan_history(self):
        log_channel = self.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            print(f"ERRO: Canal de log com ID {LOG_CHANNEL_ID} não encontrado.")
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        processed_count = 0
        async for message in log_channel.history(limit=None, oldest_first=True):
            # Alteração aqui: Verificando pelo ID do autor da mensagem
            if message.author.id == LOG_BOT_ID and message.embeds:
                embed = message.embeds[0]
                if embed.title and "Log de Compra" in embed.title:
                    cursor.execute("SELECT id FROM sales WHERE message_id = ?", (message.id,))
                    if cursor.fetchone() is None:
                        attendant_name = next((field.value.strip().replace('@', '') for field in embed.fields if field.name == "Atendente"), None)
                        if attendant_name:
                            guild = self.get_guild(GUILD_ID)
                            attendant_member = discord.utils.get(guild.members, name=attendant_name)
                            attendant_id = attendant_member.id if attendant_member else 0
                            cursor.execute(
                                "INSERT INTO sales (message_id, attendant_id, attendant_name) VALUES (?, ?, ?)",
                                (message.id, attendant_id, attendant_name)
                            )
                            processed_count += 1
        conn.commit()
        conn.close()
        print(f'Varredura concluída! {processed_count} novas vendas históricas foram registradas.')
        await self.update_total_sales_message()

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return

        # Nova venda - Alteração aqui: Verificando pelo ID do autor da mensagem
        if message.channel.id == LOG_CHANNEL_ID and message.author.id == LOG_BOT_ID and message.embeds:
            embed = message.embeds[0]
            if embed.title and "Log de Compra" in embed.title:
                attendant_name = next((field.value.strip().replace('@', '') for field in embed.fields if field.name == "Atendente"), None)
                if attendant_name:
                    guild = self.get_guild(GUILD_ID)
                    attendant_member = discord.utils.get(guild.members, name=attendant_name)
                    attendant_id = attendant_member.id if attendant_member else 0
                    conn = sqlite3.connect(DB_PATH)
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO sales (message_id, attendant_id, attendant_name) VALUES (?, ?, ?)",
                        (message.id, attendant_id, attendant_name)
                    )
                    conn.commit()
                    if attendant_id != 0:
                        cursor.execute("SELECT COUNT(*) FROM sales WHERE attendant_id = ?", (attendant_id,))
                        sales_count = cursor.fetchone()[0]
                        if sales_count > 0 and sales_count % 10 == 0:
                            salary_channel = self.get_channel(SALARY_CHANNEL_ID)
                            if salary_channel:
                                await salary_channel.send(f"🎉 Parabéns {attendant_member.mention}! Você alcançou a marca de **{sales_count}** vendas!")
                    conn.close()
                    await self.update_total_sales_message()

        # Correção de atendente (não precisa de alteração)
        if message.channel.id == LOG_CHANNEL_ID and message.content.lower().startswith("atendente") and message.mentions:
            corrected_attendant = message.mentions[0]
            message_to_correct = None
            async for old_message in message.channel.history(limit=10, before=message):
                # Aqui também verificamos pelo ID, para garantir
                if old_message.author.id == LOG_BOT_ID and old_message.embeds:
                    if old_message.embeds[0].title and "Log de Compra" in old_message.embeds[0].title:
                        message_to_correct = old_message
                        break
            if message_to_correct:
                view = CorrectionView(message_to_correct.id, corrected_attendant)
                await message.reply(f"Você deseja corrigir o atendente da última venda para {corrected_attendant.mention}?", view=view)

    async def update_total_sales_message(self):
        salary_channel = self.get_channel(SALARY_CHANNEL_ID)
        if not salary_channel:
            return
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sales")
        total_sales = cursor.fetchone()[0]
        conn.close()
        message_to_edit = None
        async for msg in salary_channel.history(limit=100):
            if msg.author == self.user and "Total de Vendas Registradas" in msg.content:
                message_to_edit = msg
                break
        content = f"📊 **Total de Vendas Registradas:** {total_sales}"
        if message_to_edit:
            await message_to_edit.edit(content=content)
        else:
            await salary_channel.send(content)

# --- COMANDOS E INICIALIZAÇÃO ---
bot = IsraBuyBot()

@bot.tree.command(name="salario", description="Calcula o salário de um atendente com base nas vendas.")
@app_commands.describe(membro="O membro para calcular o salário.")
async def salario(interaction: discord.Interaction, membro: discord.Member):
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sales WHERE attendant_id = ?", (membro.id,))
    sales_count = cursor.fetchone()[0]
    conn.close()
    salary_robux = sales_count * COMISSAO_POR_VENDA_ROBUX
    salary_brl = sales_count * COMISSAO_POR_VENDA_BRL
    progresso_meta = (salary_brl / META_BRL) * 100 if META_BRL > 0 else 0
    embed = discord.Embed(title=f"💰 Salário de {membro.display_name}", color=discord.Color.gold())
    embed.set_thumbnail(url=membro.display_avatar.url)
    embed.add_field(name="Vendas Realizadas", value=f"`{sales_count}`", inline=True)
    embed.add_field(name="Comissão (Robux)", value=f"`R$ {salary_robux}`", inline=True)
    embed.add_field(name="Comissão (BRL)", value=f"`R$ {salary_brl:.2f}`", inline=True)
    embed.add_field(name=f"🎯 Meta de Comissão (R$ {META_BRL:.2f})", value=f"Progresso: **{progresso_meta:.2f}%** (`{sales_count}` de `{META_VENDAS}` vendas)", inline=False)
    embed.set_footer(text=f"ID do Atendente: {membro.id}")
    await interaction.followup.send(embed=embed)

if __name__ == "__main__":
    setup_database()
    if BOT_TOKEN:
        bot.run(BOT_TOKEN)
    else:
        print("ERRO CRÍTICO: O token do bot (DISCORD_TOKEN) não foi encontrado. Verifique suas variáveis de ambiente na Railway.")
